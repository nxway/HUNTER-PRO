"""
enrich/dgis.py — 2ГИС как добивка (этап 2 ТЗ: "затем 2ГИС как добивка"),
когда у сайта компании нет телефона или сайт не нашёлся вовсе.

Контракт для этого модуля в разделе 5.2 ТЗ не задан (там перечислены только
enrich/inn.py, enrich/fns_bulk.py, enrich/site.py, enrich/risk.py) — добавлен
по необходимости этапа 2, сигнатура выбрана по аналогии с enrich/site.py.
Не путать с будущим sources/dgis.py (раздел 2.3, этап 9) — тот будет
самостоятельным источником лидов (сканирует рубрики 2ГИС ради новых
компаний), а этот модуль — только точечная добивка телефона для компании,
которая уже есть в базе.

СТАТУС ПРОВЕРКИ: не проверено на живых данных — 2GIS не открывается из этой
песочницы (сетевой прокси блокирует произвольные внешние домены целиком).
Сценарий и селекторы карточки организации ниже — по общей структуре 2ГИС
(поиск → карточка с телефоном/адресом/сайтом в правой панели), НЕ сверены
вживую. Перед первым боевым прогоном:
  1. Открыть 2gis.ru, найти любую организацию по названию.
  2. DevTools → Inspect на телефоне/адресе/сайте в карточке.
  3. Поправить только селекторы в find_phone() ниже (помечены ЗОНА РИСКА).

Требует playwright (см. requirements.txt) и установленный браузер:
    playwright install chromium

Запуск как отдельный модуль:
    python -m enrich.dgis --name "ООО Ромашка Плюс" --city "Москва"
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Optional

import config


class DgisLookupError(RuntimeError):
    """Страница открылась, но карточку организации разобрать не удалось."""


@dataclass
class DgisResult:
    phone: Optional[str] = None
    address: Optional[str] = None
    site: Optional[str] = None


def _clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return "+" + digits if len(digits) == 11 and digits.startswith("7") else raw.strip()


def find_phone(name: str, city: Optional[str] = None) -> DgisResult:
    """Ищет организацию в 2ГИС по названию (+ городу, если известен),
    возвращает телефон/адрес/сайт первой подходящей карточки. Пустой
    результат (все поля None) — организацию не нашли, это не ошибка."""
    from playwright.sync_api import sync_playwright

    query = f"{city} {name}" if city else name

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=config.USER_AGENT)
                # ЗОНА РИСКА: адрес поиска 2ГИС.
                page.goto(f"https://2gis.ru/search/{query}", timeout=config.HTTP_TIMEOUT * 1000)
                # ЗОНА РИСКА: селекторы карточки организации — сверить в браузере.
                page.wait_for_selector("[data-testid='ProfileTypeMainInfo']", timeout=10000)
                phone = _text(page, "[data-testid='PhoneButtonRegularItem']")
                address = _text(page, "[data-testid='ProfileAddressRow']")
                site = _href(page, "a[data-testid='ProfileWebsiteRow']")
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 — браузер не запустился, карточки нет, вёрстка другая
        raise DgisLookupError(str(exc)) from exc

    return DgisResult(
        phone=_clean_phone(phone) if phone else None,
        address=address,
        site=site,
    )


def _text(page, selector: str) -> Optional[str]:
    el = page.query_selector(selector)
    return el.inner_text().strip() if el else None


def _href(page, selector: str) -> Optional[str]:
    el = page.query_selector(selector)
    return el.get_attribute("href") if el else None


def _main() -> None:
    parser = argparse.ArgumentParser(description="Поиск телефона организации в 2ГИС")
    parser.add_argument("--name", required=True)
    parser.add_argument("--city", default=None)
    args = parser.parse_args()

    from rich.console import Console

    console = Console()
    try:
        result = find_phone(args.name, args.city)
    except DgisLookupError as exc:
        console.print(f"[red]dgis: не удалось разобрать карточку — {exc}[/red]")
        raise SystemExit(1)

    console.print(f"dgis: телефон={result.phone} адрес={result.address} сайт={result.site}")


if __name__ == "__main__":
    _main()
