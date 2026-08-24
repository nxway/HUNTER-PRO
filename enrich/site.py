"""
enrich/site.py — сайт компании → текст, телефоны, ИНН из подвала, email.
Контракт 5.2 ТЗ: fetch_site_text(url) -> SiteData.

Разбор HTML (parse_site_html) сознательно вынесен отдельной чистой функцией
от похода в сеть (fetch_site_text) — так его можно проверить на подставленном
HTML без интернета. Само скачивание страницы (fetch_site_text) в этой сессии
не проверено вживую: сетевой прокси песочницы блокирует произвольные внешние
домены, не только *.gov.ru (см. коммит этапа 1) — значит, ни один живой сайт
отсюда не открыть. Логика скачивания написана штатно (httpx, User-Agent из
конфига, таймаут), проверить на реальном сайте нужно на своей машине.

find_site() и CLI-раннер ниже — часть этапа 2 ("Ищем по ИНН и названию: сайт
компании... затем 2ГИС как добивка"), сигнатура не задана контрактом 5.2 ТЗ
(там только fetch_site_text), добавлена по необходимости этапа. Поиск сайта
идёт через DuckDuckGo HTML (html.duckduckgo.com/html/) — не требует JS/
браузера в отличие от Яндекса и Google, поэтому не нужен Playwright. Ни
запрос, ни разбор результатов НЕ проверены вживую по той же причине, что и
fetch_site_text — см. ЗОНА РИСКА у _parse_search_results().

Запуск как отдельный модуль — обогащает телефон/сайт/город для компаний,
у которых их ещё нет, и пересчитывает корзины (score.assign_buckets):
    python -m enrich.site --limit 50
"""
from __future__ import annotations

import argparse
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote

import httpx
from selectolax.parser import HTMLParser

import config
from enrich.inn import validate as validate_inn

_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-\(]*\d{3}[\)\s\-]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_INN_NEAR_RE = re.compile(r"инн[\s:№]*?(\d{10}|\d{12})", re.IGNORECASE)

SEARCH_URL = "https://html.duckduckgo.com/html/"
_DDG_REDIRECT_RE = re.compile(r"uddg=([^&]+)")


@dataclass
class SiteData:
    text: str
    phones: list[str] = field(default_factory=list)
    inn: Optional[str] = None
    emails: list[str] = field(default_factory=list)


def _clean_phone(raw: str) -> str:
    """Приводит найденный телефон к формату +7XXXXXXXXXX."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return raw.strip()


def parse_site_html(html: str) -> SiteData:
    """Чистая функция разбора: HTML → текст, телефоны, email, ИНН.
    Без сети, проверяется на подставленном HTML."""
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript"):
        tag.decompose()
    text = tree.body.text(separator=" ", strip=True) if tree.body else ""

    phones = sorted({_clean_phone(m.group(0)) for m in _PHONE_RE.finditer(text)})
    emails = sorted({m.group(0) for m in _EMAIL_RE.finditer(text)})

    inn = None
    for match in _INN_NEAR_RE.finditer(text):
        candidate = match.group(1)
        if validate_inn(candidate):
            inn = candidate
            break

    return SiteData(text=text, phones=phones, inn=inn, emails=emails)


def fetch_site_text(url: str) -> SiteData:
    """Скачивает страницу и разбирает её через parse_site_html().
    Не краулер: заходит только на переданный url (обычно главная), футер
    с реквизитами есть почти на каждой странице сайта."""
    headers = {"User-Agent": config.USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=config.HTTP_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        html = resp.text
    return parse_site_html(html)


def _unwrap_ddg_redirect(href: str) -> str:
    """DuckDuckGo HTML заворачивает внешние ссылки в редирект вида
    //duckduckgo.com/l/?uddg=<urlencoded>&... — достаём настоящий адрес."""
    match = _DDG_REDIRECT_RE.search(href)
    return unquote(match.group(1)) if match else href


def _parse_search_results(html: str) -> Optional[str]:
    """ЗОНА РИСКА: селектор первого результата поиска. Проверить в браузере
    (или curl) при первом реальном запуске — класс `result__a` соответствует
    документированной разметке html.duckduckgo.com/html/, но сайты меняют
    вёрстку без предупреждения."""
    tree = HTMLParser(html)
    link = tree.css_first("a.result__a")
    if not link:
        return None
    href = link.attributes.get("href")
    return _unwrap_ddg_redirect(href) if href else None


def find_site(name: str, city: Optional[str] = None) -> Optional[str]:
    """Ищет официальный сайт компании через поисковик. Возвращает URL
    первого результата или None — дальше решает вызывающий код (проверить
    сайт через fetch_site_text, при неудаче звать enrich.dgis)."""
    query = f"{name} {city} официальный сайт" if city else f"{name} официальный сайт"
    headers = {"User-Agent": config.USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=config.HTTP_TIMEOUT) as client:
        resp = client.get(SEARCH_URL, params={"q": query})
        resp.raise_for_status()
        html = resp.text
    return _parse_search_results(html)


def enrich_missing_phones(conn, limit: int, console=None) -> dict[str, int]:
    """Обходит компании без телефона: сайт -> (при неудаче) 2ГИС.
    Переиспользуется и из CLI этого модуля, и из hunter.py run — логика
    сама по себе от способа запуска не зависит (1.1.7 ТЗ)."""
    import db
    from enrich import dgis

    rows = conn.execute(
        """
        SELECT inn, name, city, site FROM companies
        WHERE phone IS NULL OR phone = ''
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    via_site = via_dgis = not_found = 0
    for row in rows:
        inn, name, city, site = row["inn"], row["name"], row["city"], row["site"]
        found_phone: Optional[str] = None
        found_site = site
        found_city = city
        phone_source: Optional[str] = None

        try:
            if not found_site:
                found_site = find_site(name, city)
            if found_site:
                data = fetch_site_text(found_site)
                if data.phones:
                    found_phone = data.phones[0]
                    phone_source = "site"
        except httpx.HTTPError as exc:
            if console:
                console.print(f"[yellow]{inn}: сайт недоступен ({exc}), пробую 2ГИС[/yellow]")

        if not found_phone:
            try:
                result = dgis.find_phone(name, city)
                if result.phone:
                    found_phone = result.phone
                    found_site = found_site or result.site
                    phone_source = "dgis"
            except dgis.DgisLookupError as exc:
                if console:
                    console.print(f"[yellow]{inn}: 2ГИС не дал результата ({exc})[/yellow]")

        fields = {}
        if found_phone:
            fields["phone"] = found_phone
        if found_site and found_site != site:
            fields["site"] = found_site
        if found_city and found_city != city:
            fields["city"] = found_city

        if fields:
            db.apply_enrichment(conn, inn, fields)

        if phone_source == "site":
            via_site += 1
        elif phone_source == "dgis":
            via_dgis += 1
        else:
            not_found += 1

        time.sleep(random.uniform(*config.REQUEST_DELAY))

    return {"checked": len(rows), "via_site": via_site, "via_dgis": via_dgis, "not_found": not_found}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Обогащение телефоном/сайтом + раскладка по корзинам")
    parser.add_argument("--limit", type=int, default=100, help="максимум компаний за прогон")
    args = parser.parse_args()

    import db
    import score
    from rich.console import Console

    console = Console()
    conn = db.init_db()

    stats = enrich_missing_phones(conn, args.limit, console=console)
    counts = score.assign_buckets(conn)
    conn.close()
    console.print(
        f"enrich.site: проверено {stats['checked']} · нашли на сайте {stats['via_site']} "
        f"· нашли в 2ГИС {stats['via_dgis']} · не нашли {stats['not_found']} "
        f"· зелёных {counts['green']} · жёлтых {counts['yellow']} · красных {counts['red']}"
    )


if __name__ == "__main__":
    _main()
