"""
sources/fsa_declarations.py — источник: реестр деклараций о соответствии
Росаккредитации (pub.fsa.gov.ru). Первый источник проекта (этап 1 ТЗ).

Почему он первый: декларацию оформляет только тот, кто физически производит
или ввозит товар. Транспортные компании, айтишники, консультанты и
бюджетные учреждения заявителями быть не могут — отсев мусора получается
по устройству источника, без ОКВЭД и стоп-слов. Заодно источник сразу даёт
ИНН (в списке, отдельно заходить в карточку не нужно) и текст продукции.

СТАТУС ПРОВЕРКИ — история трёх реальных попыток:
  1. Изначально написан вслепую по угаданной структуре HTML — угадал
     неверно: это Angular-приложение с JSON-API, не серверный HTML.
  2. Переписан под реальный API (POST /login → JWT в заголовке ответа →
     POST /api/v1/rds/common/declarations/get), снятый пользователем через
     DevTools. Живой прогон через httpx получил 403 Forbidden на /login.
  3. Добавлены браузероподобные заголовки (User-Agent Chrome, Origin,
     Sec-Fetch-*) — 403 остался. Значит, дело не в заголовках: сайт стоит
     за Bitrix (кука BITRIX_CONVERSION_CONTEXT_s1 в снятом дампе), и
     похоже, что защита смотрит на то, что HTTP-клиент не подделает в
     принципе — отпечаток TLS-соединения и/или реальное исполнение JS
     страницы перед вызовом API. Единственный надёжный способ пройти
     такую защиту — не подделывать браузер, а быть браузером: источник
     переведён на Playwright (уже зависимость проекта — изначально для
     2ГИС, новой не добавляли). Логин и запрос деклараций выполняются
     через fetch() ВНУТРИ страницы, реально загруженной в Chromium —
     тот же TLS/JS-контекст, что и у настоящего пользователя. Заодно
     отпала нужда вручную генерировать session-cookie: её выставляет
     собственный JS сайта при обычной загрузке страницы.

Ни разу не проверено вживую до конца (сеть песочницы по-прежнему
заблокирована) — только по шагам, которые сообщал пользователь. Если и
это не пройдёт — следующая причина, скорее всего, IP-блокировка или более
глубокая защита, для которых нужен уже другой разговор (снова смотреть
реальный ответ).

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

import config
from enrich.inn import region_of, validate as validate_inn
from sources.base import RawLead, SourceSpec, save_raw

SPEC = SourceSpec(
    key="fsa_declarations",
    title="Декларации о соответствии (Росаккредитация)",
    kind="event",
    signal_type="new_declaration",
    gives_inn=True,
    needs_browser=True,  # антибот-защита сайта не пропускает обычный HTTP-клиент, см. докстринг
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

REGISTRY_PAGE_URL = "https://pub.fsa.gov.ru/rds/declaration"
LOGIN_URL = "https://pub.fsa.gov.ru/login"
DECLARATIONS_URL = "https://pub.fsa.gov.ru/api/v1/rds/common/declarations/get"

# ЗОНА РИСКА: логин-пароль анонимного доступа зашиты в JS-бандл самого
# сайта — это не наш секрет и не платный ключ, просто механика их
# анонимной авторизации. Если перестанет работать, скорее всего сайт
# сменил эту пару — искать в Network на запросе POST /login.
_ANON_USERNAME = "anonymous"
_ANON_PASSWORD = "hrgesf7HDR67Bd"

# ЗОНА РИСКА: реальный интерфейс сайта запрашивает по 10 записей за раз —
# больше не проверено, сервер может резать. Если начнёт падать или
# возвращать пустые/усечённые страницы — понизить до 10.
_PAGE_SIZE = 50

_CITY_RE = re.compile(r"г(?:ород)?\.?\s+([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+){0,2})")

_LOGIN_JS = """
async ([username, password]) => {
    const resp = await fetch('%s', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password})
    });
    if (resp.ok) {
        return {status: resp.status, ok: true, token: resp.headers.get('Authorization')};
    }
    return {status: resp.status, ok: false, text: await resp.text()};
}
""" % LOGIN_URL

_DECLARATIONS_JS = """
async ([token, payload]) => {
    const resp = await fetch('%s', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Authorization': token},
        body: JSON.stringify(payload)
    });
    if (resp.ok) {
        return {status: resp.status, ok: true, data: await resp.json()};
    }
    return {status: resp.status, ok: false, text: await resp.text()};
}
""" % DECLARATIONS_URL


class FsaParseError(RuntimeError):
    """Ответ пришёл, но его не удалось разобрать — формат API другой, или
    сайт отказал (антибот/бан) — текст ошибки содержит, что именно."""


def _build_payload(date_from: date, date_to: date, page: int) -> dict:
    """Тело запроса — снято живьём с реального поиска на сайте (24.08.2026),
    не угадано. page нумеруется с нуля, как в реальном запросе."""
    return {
        "size": _PAGE_SIZE,
        "page": page,
        "count": 0,
        "filter": {
            "status": [],
            "idDeclType": [],
            "idCertObjectType": [],
            "idProductType": [],
            "idGroupRU": [],
            "idGroupEEU": [],
            "idTechReg": [],
            "idApplicantType": [],
            "regDate": {"minDate": date_from.isoformat(), "maxDate": date_to.isoformat()},
            "endDate": {"minDate": None, "maxDate": None},
            "columnsSearch": [{"name": "number", "search": None, "type": 0}],
            "number": None,
            "idProductOrigin": [],
            "idProductEEU": [],
            "idProductRU": [],
            "idDeclScheme": [],
            "awaitOperatorCheck": None,
            "editApp": None,
            "violationSendDate": None,
            "isProtocolInvalid": None,
            "checkerAIResult": None,
            "checkerAIProtocolsResults": None,
            "checkerAIProtocolsMistakes": None,
            "hiddenFromOpen": None,
        },
        "columnsSort": [{"column": "declDate", "sort": "DESC"}],
    }


def _extract_city(address: Optional[str]) -> Optional[str]:
    """applicantAddress — полный адрес одной строкой, вытаскиваем город по
    "г Город"/"город Город". Не разобрался — просто None, это не мусор,
    это нехватка данных (7.1 ТЗ), сайт/2ГИС на этапе 2 могут дополнить."""
    if not address:
        return None
    match = _CITY_RE.search(address)
    return match.group(1) if match else None


def _product_summary(item: dict) -> Optional[str]:
    """Первое непустое из group/productFullName/productIdentificationName —
    реальные поля ответа не всегда все заполнены одновременно."""
    for field in ("group", "productFullName", "productIdentificationName"):
        value = item.get(field)
        if value and value.strip():
            return value.strip()
    return None


def _iter_raw_items(days: int, limit: int, delay_lo: float, delay_hi: float) -> Iterator[dict]:
    """Через настоящий Chromium: логин и запрос деклараций идут как
    fetch() внутри реально загруженной страницы — тот же TLS/JS-контекст,
    что у настоящего пользователя (см. докстринг модуля, почему это
    понадобилось)."""
    from playwright.sync_api import sync_playwright

    date_to = date.today()
    date_from = date_to - timedelta(days=days)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(REGISTRY_PAGE_URL, wait_until="networkidle", timeout=config.HTTP_TIMEOUT * 1000)

            login_result = page.evaluate(_LOGIN_JS, [_ANON_USERNAME, _ANON_PASSWORD])
            if not login_result.get("ok") or not login_result.get("token"):
                raise FsaParseError(
                    f"вход не удался: статус {login_result.get('status')}, "
                    f"ответ: {login_result.get('text', '')[:500]!r}"
                )
            token = login_result["token"]

            page_num = 0
            yielded = 0
            while yielded < limit:
                payload = _build_payload(date_from, date_to, page_num)
                result = page.evaluate(_DECLARATIONS_JS, [token, payload])
                if not result.get("ok"):
                    raise FsaParseError(
                        f"запрос деклараций вернул {result.get('status')}: "
                        f"{result.get('text', '')[:500]!r}"
                    )
                data = result["data"]
                save_raw(SPEC.key, f"declarations_p{page_num}", str(data))
                items = data.get("items", [])

                if not items:
                    break

                for item in items:
                    yield item
                    yielded += 1
                    if yielded >= limit:
                        break

                if len(items) < _PAGE_SIZE:
                    break  # последняя страница короче полной — дальше пусто

                page_num += 1
                time.sleep(random.uniform(delay_lo, delay_hi))
        finally:
            browser.close()


def collect(settings: dict) -> Iterator[RawLead]:
    """Единственная обязательная функция источника (5.1 ТЗ)."""
    regions = set(settings.get("regions") or config.TARGET_REGIONS)
    days = int(settings.get("days", SPEC.default_settings["days"]))
    limit = int(settings.get("limit", SPEC.default_settings["limit"]))
    delay_lo, delay_hi = settings.get("delay_sec", config.REQUEST_DELAY)

    date_to = date.today()

    for item in _iter_raw_items(days, limit, delay_lo, delay_hi):
        number = item.get("number") or ""
        inn = item.get("creatorInn")
        if not number or not inn or not validate_inn(inn):
            continue  # без номера или без валидного ИНН — брак, не связываем (15.1 ТЗ)
        if region_of(inn) not in regions:
            continue

        yield RawLead(
            name=item.get("applicantName") or "",
            source="fsa",
            signal_type=SPEC.signal_type,
            signal_date=item.get("declDate") or date_to.isoformat(),
            dedup_key=f"fsa|{SPEC.signal_type}|{number}",
            url=None,
            summary=_product_summary(item),
            product_code=None,
            city=_extract_city(item.get("applicantAddress")),
            site=None,
            phone=None,
            inn=inn,
            raw_path=None,
        )


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
        console.print(f"[red]fsa_declarations: сбой — {exc}[/red]")
        sys.exit(1)
    finally:
        conn.close()

    console.print(
        f"fsa_declarations: собрано {collected} · новых компаний {new_companies} "
        f"· новых сигналов {new_signals} · регионы {settings['regions']} · дней {settings['days']}"
    )


if __name__ == "__main__":
    _main()
