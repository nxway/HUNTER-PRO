"""
enrich/risk.py — красные флаги: Федресурс (банкротство, намерение о
банкротстве), ФССП (исполнительные производства). Контракт 5.2 ТЗ:
check(inn) -> RiskResult.

Риск проверяется точечно — для десятков-сотен компаний перед постановкой
в очередь звонков (16.4 ТЗ), не для всей базы. Поэтому это не источник
(sources/*.py), а enrich-модуль, вызываемый по требованию.

Правило 16.3 ТЗ, важное: красный флаг не выбрасывает лида, он меняет
условия работы с ним. Риск НЕ подмешивается в score.py/корзины — это
отдельное поле "Риск" в выгрузке, которое ты читаешь глазами.

Арбитражная картотека (kad.arbitr.ru) сюда сознательно не включена —
раздел 16.2 ТЗ прямо просит оставить её на ручную проверку: сайт активно
защищается от автосбора. ЕГРЮЛ-статусы (ликвидация/реорганизация/
недостоверность) тоже не автоматизированы: раздел 15.2 называет
egrul.nalog.ru "ручным инструментом на единичные случаи", массовый разбор
там не тот же режим, что ФССП/Федресурс — если понадобится, добавляется
отдельно, не выдумываю сейчас.

СТАТУС ПРОВЕРКИ: не проверено на живых данных — сеть песочницы блокирует
все внешние домены. ФССП и Федресурс собраны по описанию из раздела 16.2
ТЗ (публичный REST по токену) — точный путь запроса и разбор ответа ЗОНА
РИСКА, см. _check_fssp/_check_fedresurs. Без токена (FSSP_API_TOKEN /
FEDRESURS_API_TOKEN в .env) соответствующая проверка просто пропускается,
не роняя прогон — это осознанный выбор, а не заглушка: часть флагов лучше,
чем падение на пустом месте.

Запуск как отдельный модуль:
    python -m enrich.risk --inn 7707083893
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

import config

# ЗОНА РИСКА: пути запросов не проверены вживую.
FSSP_URL = "https://api-ip.fssp.gov.ru/api/v1.0/entrp/search"
FEDRESURS_URL = "https://fedresurs.ru/backend/companies/search"

_STOP_MARKERS = ("банкротство", "ликвидация", "недостоверн")


@dataclass
class RiskResult:
    level: str = "ok"  # 'ok' | 'watch' | 'stop'
    flags: list[str] = field(default_factory=list)


def _check_fssp(inn: str) -> list[str]:
    """ЗОНА РИСКА: путь запроса и форма ответа api-ip.fssp.gov.ru не
    проверены. Сумма производств важнее их количества (16.2 ТЗ) — поэтому
    флаг один, с суммой, а не список отдельных производств."""
    if not config.FSSP_API_TOKEN:
        return []
    headers = {"User-Agent": config.USER_AGENT}
    params = {"token": config.FSSP_API_TOKEN, "type": 2, "inn": inn}
    with httpx.Client(headers=headers, timeout=config.HTTP_TIMEOUT) as client:
        resp = client.get(FSSP_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    items = data if isinstance(data, list) else data.get("result", [])
    total_debt = 0.0
    for item in items:
        for proceeding in item.get("исполнительные_производства", []) or []:
            total_debt += proceeding.get("остаток_основного_долга", 0) or 0

    if total_debt > 0:
        return [f"долги у приставов: {total_debt:,.0f} ₽".replace(",", " ")]
    return []


def _check_fedresurs(inn: str) -> list[str]:
    """ЗОНА РИСКА: путь запроса и форма ответа fedresurs.ru не проверены."""
    if not config.FEDRESURS_API_TOKEN:
        return []
    headers = {
        "User-Agent": config.USER_AGENT,
        "Authorization": f"Bearer {config.FEDRESURS_API_TOKEN}",
    }
    with httpx.Client(headers=headers, timeout=config.HTTP_TIMEOUT) as client:
        resp = client.get(FEDRESURS_URL, params={"inn": inn})
        resp.raise_for_status()
        data = resp.json()

    flags: list[str] = []
    messages = data.get("messages", []) if isinstance(data, dict) else []
    for msg in messages:
        msg_type = (msg.get("type") or "").lower()
        if "намерени" in msg_type and "банкрот" in msg_type:
            flags.append("намерение о банкротстве")
        elif "банкрот" in msg_type:
            flags.append("банкротство")
    return flags


def check(inn: str) -> RiskResult:
    """Собирает флаги риска по ИНН из доступных источников. Сбой одного
    источника не роняет проверку целиком — просто даёт меньше флагов
    (тот же принцип устойчивости, что и в остальном проекте, раздел X ТЗ)."""
    flags: list[str] = []
    for checker in (_check_fssp, _check_fedresurs):
        try:
            flags.extend(checker(inn))
        except httpx.HTTPError:
            continue

    level = "ok"
    if any(marker in f for f in flags for marker in _STOP_MARKERS):
        level = "stop"
    elif flags:
        level = "watch"

    return RiskResult(level=level, flags=flags)


def check_pending(conn, limit: int = 200) -> dict[str, int]:
    """Пакетная проверка риска для ближайших кандидатов на звонок — не для
    всей базы (16.4 ТЗ: точечно, перед постановкой в очередь). Перепроверка
    раз в 30 дней. Единственный офлайн-флаг, который не берётся из сети:
    падение выручки два года подряд — это уже посчитано в companies
    (enrich/fns_bulk.py, revenue_trend), сеть за ним ходить не нужно."""
    rows = conn.execute(
        """
        SELECT inn, revenue_trend FROM companies
        WHERE bucket = 'green'
          AND (risk_checked_at IS NULL OR julianday('now') - julianday(risk_checked_at) > 30)
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {"ok": 0, "watch": 0, "stop": 0}
    for row in rows:
        result = check(row["inn"])
        flags = list(result.flags)
        level = result.level
        if row["revenue_trend"] == "падение":
            flags.append("выручка падает")
            if level == "ok":
                level = "watch"

        conn.execute(
            "UPDATE companies SET risk_level = ?, risk_flags = ?, risk_checked_at = ? WHERE inn = ?",
            (level, json.dumps(flags, ensure_ascii=False), now, row["inn"]),
        )
        counts[level] += 1

    conn.commit()
    return counts


def _main() -> None:
    parser = argparse.ArgumentParser(description="Проверка риска по ИНН")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--inn", help="разовая проверка одного ИНН")
    group.add_argument("--pending", action="store_true", help="пакетная проверка кандидатов на звонок")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    from rich.console import Console

    console = Console()
    if args.inn:
        result = check(args.inn)
        console.print(f"risk: {args.inn} -> уровень={result.level} флаги={result.flags}")
    else:
        import db

        conn = db.init_db()
        counts = check_pending(conn, args.limit)
        conn.close()
        console.print(f"risk: ok={counts['ok']} watch={counts['watch']} stop={counts['stop']}")


if __name__ == "__main__":
    _main()
