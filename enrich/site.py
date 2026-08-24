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
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from selectolax.parser import HTMLParser

import config
from enrich.inn import validate as validate_inn

_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-\(]*\d{3}[\)\s\-]*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_INN_NEAR_RE = re.compile(r"инн[\s:№]*?(\d{10}|\d{12})", re.IGNORECASE)


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
