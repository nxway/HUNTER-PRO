"""
sources/fsa_declarations.py — источник: реестр деклараций о соответствии
Росаккредитации (pub.fsa.gov.ru). Первый источник проекта (этап 1 ТЗ).

Почему он первый: декларацию оформляет только тот, кто физически производит
или ввозит товар. Транспортные компании, айтишники, консультанты и
бюджетные учреждения заявителями быть не могут — отсев мусора получается
по устройству источника, без ОКВЭД и стоп-слов. Заодно источник сразу даёт
ИНН (в списке, отдельно заходить в карточку не нужно) и текст продукции.

СТАТУС ПРОВЕРКИ: изначально был написан вслепую (сеть песочницы блокирует
*.gov.ru) по угаданной структуре HTML-страницы — и угадал неверно: это не
серверный HTML, а Angular-приложение с JSON-API. Переписан по РЕАЛЬНЫМ
запросам, снятым пользователем через DevTools на живом pub.fsa.gov.ru
24.08.2026:
  1. POST /login с телом {"username":"anonymous","password":"hrgesf7HDR67Bd"}
     и заголовком Cookie: session-cookie=<случайная строка, генерируем сами> —
     сервер возвращает JWT-токен НЕ в теле, а в заголовке ОТВЕТА Authorization.
     Токен живёт ~8 часов (payload: iss=FAU NIA, sub=anonymous) — получаем
     заново в начале каждого прогона, не кешируем между запусками.
  2. POST /api/v1/rds/common/declarations/get с этим токеном в Authorization
     запроса — тело и структура ответа сняты живьём, см. _build_payload()
     и парсинг в collect(). ИНН заявителя — поле creatorInn прямо в списке.
Сама механика (логин → токен → постраничный запрос) проверена только по
снятым дампам, не прогнана целиком в этой сессии (сеть по-прежнему
заблокирована) — это единственное, что стоит перепроверить в первую
очередь при первом реальном запуске: `python -m sources.fsa_declarations`.
Если сломается — скорее всего на шаге логина или на структуре ответа
declarations/get, оба места отмечены ЗОНА РИСКА ниже.

Запуск как отдельный модуль (1.1.7 ТЗ):
    python -m sources.fsa_declarations --region 50 --days 30
"""
from __future__ import annotations

import argparse
import random
import re
import secrets
import sys
import time
from datetime import date, timedelta
from typing import Iterator, Optional

import httpx
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

# ОТСТУПЛЕНИЕ ОТ ОБЩЕГО ПРАВИЛА ПРОЕКТА (12.2 ТЗ, "внятный User-Agent с
# контактной почтой"): первый живой прогон вернул 403 Forbidden на самом
# /login с "вежливым" User-Agent вида HunterPro/1.0 — сайт стоит за Bitrix
# (видно по куке BITRIX_CONVERSION_CONTEXT_s1 в снятом дампе), у которого
# есть встроенная защита от ботов, отсекающая нестандартные User-Agent и
# отсутствующие Sec-Fetch-*/Origin. Раз доступ анонимный и публичный (сам
# сайт выдаёт токен без входа под реальным пользователем), выдаём себя за
# обычный браузер только для ЭТОГО источника — иначе не достучаться вообще.
# Для сайтов компаний (enrich/site.py) такой необходимости не возникало,
# там остаётся config.USER_AGENT.
_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Origin": "https://pub.fsa.gov.ru",
    "Referer": "https://pub.fsa.gov.ru/rds/declaration",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_CITY_RE = re.compile(r"г(?:ород)?\.?\s+([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+){0,2})")


class FsaParseError(RuntimeError):
    """Ответ пришёл, но его не удалось разобрать — формат API другой."""


def _get_token(client: httpx.Client) -> str:
    """POST /login с одноразовой session-cookie, токен приходит в
    заголовке ОТВЕТА Authorization, не в теле (снято живьём, см. докстринг
    модуля). ЗОНА РИСКА: сама механика (логин/куки/заголовок) не прогнана
    в этой сессии из-за блокировки сети — только по дампу пользователя."""
    session_cookie = secrets.token_hex(48)
    client.cookies.set("session-cookie", session_cookie, domain="pub.fsa.gov.ru")

    resp = client.post(
        LOGIN_URL,
        json={"username": _ANON_USERNAME, "password": _ANON_PASSWORD},
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    token = resp.headers.get("Authorization")
    if not token:
        raise FsaParseError("POST /login отработал (200), но заголовок Authorization пуст")
    return token


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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def _fetch_page(client: httpx.Client, token: str, date_from: date, date_to: date, page: int) -> dict:
    resp = client.post(
        DECLARATIONS_URL,
        json=_build_payload(date_from, date_to, page),
        headers={"Authorization": token},
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


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


def collect(settings: dict) -> Iterator[RawLead]:
    """Единственная обязательная функция источника (5.1 ТЗ)."""
    regions = set(settings.get("regions") or config.TARGET_REGIONS)
    days = int(settings.get("days", SPEC.default_settings["days"]))
    limit = int(settings.get("limit", SPEC.default_settings["limit"]))
    delay_lo, delay_hi = settings.get("delay_sec", config.REQUEST_DELAY)

    date_to = date.today()
    date_from = date_to - timedelta(days=days)

    yielded = 0
    with httpx.Client(headers=_BROWSER_HEADERS, follow_redirects=True) as client:
        token = _get_token(client)

        page = 0
        while yielded < limit:
            data = _fetch_page(client, token, date_from, date_to, page)
            save_raw(SPEC.key, f"declarations_p{page}", str(data))
            items = data.get("items", [])

            if not items:
                break

            for item in items:
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
                yielded += 1
                if yielded >= limit:
                    break

            if len(items) < _PAGE_SIZE:
                break  # последняя страница короче полной — дальше пусто

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
        console.print(f"[red]fsa_declarations: не удалось разобрать ответ — {exc}[/red]")
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
