"""
sources/fsa_declarations.py — источник: реестр деклараций о соответствии
Росаккредитации (pub.fsa.gov.ru). Первый источник проекта (этап 1 ТЗ).

Почему он первый: декларацию оформляет только тот, кто физически производит
или ввозит товар. Транспортные компании, айтишники, консультанты и
бюджетные учреждения заявителями быть не могут — отсев мусора получается
по устройству источника, без ОКВЭД и стоп-слов. Заодно источник сразу даёт
ИНН и код/название продукции.

СТАТУС ПРОВЕРКИ НА ЖИВЫХ ДАННЫХ: сетевой прокси этой облачной сессии
блокирует все домены *.gov.ru, поэтому запрос и разбор страницы ниже
собраны по документированному публичному поиску pub.fsa.gov.ru/rds/declaration,
но НЕ протестированы вживую из этой сессии. Перед первым реальным прогоном:
  1. Открыть https://pub.fsa.gov.ru/rds/declaration в браузере.
  2. DevTools → Network, выполнить поиск деклараций за последние дни,
     найти запрос, который возвращает список.
  3. Сверить с реальным запросом/ответом только SEARCH_URL, _build_params()
     и _parse_results() ниже — они помечены "ЗОНА РИСКА". Остальная часть
     файла (сохранение сырья, дедуп по номеру декларации, сборка RawLead,
     фильтр региона по ИНН, CLI, запись в базу) от формата ответа не зависит
     и правки не требует.

Запуск как отдельный модуль (1.1.7 ТЗ):
    python -m sources.fsa_declarations --region 50 --days 30
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from datetime import date, timedelta
from typing import Iterator, Optional

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

import config
from enrich.inn import region_of, validate as validate_inn
from sources.base import RawLead, SourceSpec, save_raw

SPEC = SourceSpec(
    key="fsa_declarations",
    title="Декларации о соответствии (Росаккредитация)",
    kind="event",
    signal_type="new_declaration",
    gives_inn=True,
    needs_browser=False,
    default_settings={
        "regions": config.TARGET_REGIONS,
        "days": 30,
        "limit": 500,
        "delay_sec": config.REQUEST_DELAY,
    },
    setting_hints={
        "regions": "Коды регионов (первые две цифры ИНН заявителя)",
        "days": "Глубина поиска в днях от сегодня",
        "limit": "Максимум записей за прогон",
        "delay_sec": "Пауза между запросами, сек (мин, макс)",
    },
)

# ЗОНА РИСКА: адрес публичного поиска деклараций.
SEARCH_URL = "https://pub.fsa.gov.ru/rds/declaration"

_INN_RE = re.compile(r"(?<!\d)(\d{10}|\d{12})(?!\d)")


class FsaParseError(RuntimeError):
    """Страница пришла, но её не удалось разобрать — вёрстка сайта другая."""


def _build_params(date_from: date, date_to: date, page: int) -> dict:
    """ЗОНА РИСКА: имена параметров подобраны по общей практике похожих
    реестров, не подтверждены живым запросом. Сверить в DevTools."""
    return {
        "status": "ACTIVE",
        "regDateFrom": date_from.isoformat(),
        "regDateTo": date_to.isoformat(),
        "page": page,
    }


def _extract_inn(text: str) -> Optional[str]:
    match = _INN_RE.search(text)
    return match.group(1) if match else None


def _parse_results(html: str) -> list[dict]:
    """ЗОНА РИСКА: разбор таблицы результатов поиска. Строки без ожидаемого
    числа ячеек молча пропускаются (мусорная строка вёрстки — не декларация),
    но если на странице вообще нет ни одной подходящей строки — это повод
    остановиться и проверить вёрстку, а не тихо решить, что деклараций нет
    (см. collect(), где page==1 и items==[] считается ошибкой разбора)."""
    tree = HTMLParser(html)
    rows = tree.css("table tbody tr")
    items = []
    for row in rows:
        cells = row.css("td")
        if len(cells) < 4:
            continue
        link = row.css_first("a")
        applicant_text = cells[2].text(strip=True)
        items.append(
            {
                "number": cells[0].text(strip=True),
                "reg_date": cells[1].text(strip=True),
                "applicant": applicant_text,
                "product": cells[3].text(strip=True),
                "inn": _extract_inn(applicant_text),
                "url": link.attributes.get("href") if link else None,
            }
        )
    return items


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def _fetch_page(client: httpx.Client, date_from: date, date_to: date, page: int) -> str:
    resp = client.get(
        SEARCH_URL,
        params=_build_params(date_from, date_to, page),
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def collect(settings: dict) -> Iterator[RawLead]:
    """Единственная обязательная функция источника (5.1 ТЗ)."""
    regions = set(settings.get("regions") or config.TARGET_REGIONS)
    days = int(settings.get("days", SPEC.default_settings["days"]))
    limit = int(settings.get("limit", SPEC.default_settings["limit"]))
    delay_lo, delay_hi = settings.get("delay_sec", config.REQUEST_DELAY)

    date_to = date.today()
    date_from = date_to - timedelta(days=days)

    headers = {"User-Agent": config.USER_AGENT}
    yielded = 0
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        page = 1
        empty_pages = 0
        while yielded < limit:
            html = _fetch_page(client, date_from, date_to, page)
            save_raw(SPEC.key, f"search_p{page}", html)
            items = _parse_results(html)

            if not items:
                if page == 1:
                    raise FsaParseError(
                        "страница результатов пришла, но ни одной декларации "
                        "не найдено на первой странице — вероятно, вёрстка "
                        "сайта другая, см. _parse_results()"
                    )
                empty_pages += 1
                if empty_pages >= 2:
                    break
                page += 1
                time.sleep(random.uniform(delay_lo, delay_hi))
                continue
            empty_pages = 0

            for item in items:
                number = item.get("number") or ""
                inn = item.get("inn")
                if not number or not inn or not validate_inn(inn):
                    continue  # без номера или без валидного ИНН — брак, не связываем
                if region_of(inn) not in regions:
                    continue

                yield RawLead(
                    name=item["applicant"],
                    source="fsa",
                    signal_type=SPEC.signal_type,
                    signal_date=item.get("reg_date") or date_to.isoformat(),
                    dedup_key=f"fsa|{SPEC.signal_type}|{number}",
                    url=item.get("url"),
                    summary=item.get("product"),
                    product_code=None,
                    city=None,
                    site=None,
                    phone=None,
                    inn=inn,
                    raw_path=None,
                )
                yielded += 1
                if yielded >= limit:
                    break

            page += 1
            time.sleep(random.uniform(delay_lo, delay_hi))


def _main() -> None:
    parser = argparse.ArgumentParser(description=SPEC.title)
    parser.add_argument("--region", type=int, action="append", help="код региона, можно несколько раз")
    parser.add_argument("--days", type=int, default=SPEC.default_settings["days"])
    parser.add_argument("--limit", type=int, default=SPEC.default_settings["limit"])
    args = parser.parse_args()

    settings = dict(SPEC.default_settings)
    if args.region:
        settings["regions"] = args.region
    settings["days"] = args.days
    settings["limit"] = args.limit

    import db  # локальный импорт: модуль должен запускаться и без hunter.py

    from rich.console import Console

    console = Console()
    conn = db.init_db()

    collected = 0
    new_companies = 0
    new_signals = 0
    try:
        for lead in collect(settings):
            result = db.ingest(conn, lead)
            collected += 1
            new_companies += int(result.company_new)
            new_signals += int(result.signal_new)
    except FsaParseError as exc:
        console.print(f"[red]fsa_declarations: не удалось разобрать страницу — {exc}[/red]")
        sys.exit(1)
    except httpx.HTTPError as exc:
        console.print(f"[red]fsa_declarations: сетевая ошибка — {exc}[/red]")
        sys.exit(1)
    finally:
        conn.close()

    console.print(
        f"fsa_declarations: собрано {collected} · новых компаний {new_companies} "
        f"· новых сигналов {new_signals} · регионы {settings['regions']} · дней {settings['days']}"
    )


if __name__ == "__main__":
    _main()
