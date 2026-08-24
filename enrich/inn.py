"""
enrich/inn.py — работа с ИНН, которая не требует интернета.

Часть XV.1 ТЗ: проверка контрольной суммы — это фильтр опечаток и мусора
из парсинга источников, а не проверка того, что фирма существует.
Всё, что не проходит validate(), в базу не попадает (см. db.ingest).

resolve() — часть XV.3-XV.5 ТЗ: разрешение ИНН по названию для источников,
которые сами ИНН не дают (будущие expo/2ГИС-как-источник). ОТСТУПЛЕНИЕ ОТ
КОНТРАКТА 5.2: там сигнатура `resolve(name, city, site) -> Resolution` без
conn — но без подключения к базе функция не может ни прочитать registry,
ни (в будущем, когда появится вызывающий код) записать неоднозначные
варианты в inn_candidates, то есть буквально нереализуема. Добавлен conn
первым параметром и опциональный okved_hint (без него скоринг 15.4 по
ОКВЭД невозможен) — остальное по контракту. Пока ни один подключённый
источник (только fsa_declarations.py) не нуждается в resolve() — он всегда
даёт ИНН напрямую — эта функция готова для источников этапа 9.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_WEIGHTS_10 = [2, 4, 10, 3, 5, 9, 4, 6, 8]
_WEIGHTS_12_D11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
_WEIGHTS_12_D12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]

_QUOTES_RE = re.compile(r'[«»"“”„\'‘’,]')
_LEGAL_FORM_RE = re.compile(r"\b(ооо|оао|зао|пао|нао|ао|ип)\b")
_WS_RE = re.compile(r"[-\s]+")


def _control_digit(digits: list[int], weights: list[int]) -> int:
    return sum(d * w for d, w in zip(digits, weights)) % 11 % 10


def validate(inn: str) -> bool:
    """Проверяет контрольную сумму ИНН (10 цифр — юрлицо, 12 — ИП)."""
    if not inn or not inn.isdigit():
        return False
    digits = [int(c) for c in inn]
    if len(digits) == 10:
        return _control_digit(digits[:9], _WEIGHTS_10) == digits[9]
    if len(digits) == 12:
        d11_ok = _control_digit(digits[:10], _WEIGHTS_12_D11) == digits[10]
        d12_ok = _control_digit(digits[:11], _WEIGHTS_12_D12) == digits[11]
        return d11_ok and d12_ok
    return False


def region_of(inn: str) -> int:
    """Первые две цифры валидного ИНН — код субъекта РФ."""
    return int(inn[:2])


def normalize_name(name: str) -> str:
    """Нормализация названия компании для сравнения (15.3 ТЗ): нижний
    регистр, ё->е, без кавычек всех видов и организационно-правовой формы,
    дефисы/пробелы схлопнуты в один. Применяется одинаково к обеим сторонам
    сравнения — иначе `ООО «Ромашка-Плюс»` и `Ромашка Плюс, ООО` не сойдутся."""
    s = (name or "").lower().replace("ё", "е")
    s = _QUOTES_RE.sub("", s)
    s = _LEGAL_FORM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


@dataclass
class Resolution:
    status: str  # 'ok' | 'ambiguous' | 'not_found'
    inn: Optional[str] = None
    method: Optional[str] = None  # 'registry' | 'site'
    candidates: list = field(default_factory=list)


def _score_candidate(cand: dict, city: Optional[str], okved_hint: Optional[str]) -> int:
    """Баллы за совпавшие признаки (15.4 ТЗ). Регистр (registry) не хранит
    домен/телефон/адрес источника — эти два критерия из таблицы 15.4 здесь
    физически недоступны, скоринг честно построен только на том, что есть
    в registry (имя, город, ОКВЭД)."""
    score = 2  # название точно совпало после нормализации — уже гарантировано запросом
    if city and cand.get("city") and cand["city"].strip().lower() == city.strip().lower():
        score += 3
    if okved_hint and cand.get("okved") and str(cand["okved"]).startswith(okved_hint):
        score += 2
    return score


def resolve(
    conn,
    name: str,
    city: Optional[str] = None,
    site: Optional[str] = None,
    okved_hint: Optional[str] = None,
) -> Resolution:
    """Разрешает ИНН по названию, когда источник его не дал. Ступени 2-4
    раздела 15.3 (ступень 1 — источник дал ИНН — не здесь, это забота
    вызывающего кода ДО resolve()):
      2. точное совпадение в registry по name_norm + город, один результат
         → берём (буквально по 15.3, без скоринга — скоринг 15.4 нужен
         только когда тёзок несколько, «одного названия не хватает никогда»);
      3. если не разрешилось — сайт (fetch_site_text + ИНН из текста);
      4. не разрешилось — Resolution(status='not_found'), не выдумываем.
    "Сомневаешься — не связывай" (15.3): при неоднозначности возвращает
    status='ambiguous' с кандидатами, ничего не привязывая."""
    name_norm = normalize_name(name)

    if city:
        narrow = [
            dict(r)
            for r in conn.execute(
                "SELECT inn, name_raw, city, okved FROM registry WHERE name_norm = ? AND lower(city) = lower(?)",
                (name_norm, city),
            ).fetchall()
        ]
        if len(narrow) == 1 and validate(narrow[0]["inn"]):
            return Resolution(status="ok", inn=narrow[0]["inn"], method="registry")

    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT inn, name_raw, city, okved FROM registry WHERE name_norm = ?",
            (name_norm,),
        ).fetchall()
    ]

    if len(rows) == 1 and validate(rows[0]["inn"]):
        return Resolution(status="ok", inn=rows[0]["inn"], method="registry")

    if rows:
        for r in rows:
            r["score"] = _score_candidate(r, city, okved_hint)
        rows.sort(key=lambda c: c["score"], reverse=True)
        best = rows[0]
        second_score = rows[1]["score"] if len(rows) > 1 else 0
        if best["score"] >= 7 and best["score"] - second_score >= 3 and validate(best["inn"]):
            return Resolution(status="ok", inn=best["inn"], method="registry")
        return Resolution(status="ambiguous", candidates=rows[:5])

    if site:
        try:
            from enrich.site import fetch_site_text

            data = fetch_site_text(site)
            if data.inn and validate(data.inn):
                return Resolution(status="ok", inn=data.inn, method="site")
        except Exception:  # noqa: BLE001 — сайт недоступен/не разобрался = не нашли, не падение
            pass

    return Resolution(status="not_found")


def queue_candidates(conn, lead_key: str, name: str, city: Optional[str], candidates: list[dict]) -> None:
    """Кладёт неоднозначные варианты в inn_candidates — отдельным листом
    в следующей выгрузке (15.5 ТЗ), решение за человеком."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for cand in candidates:
        conn.execute(
            """
            INSERT INTO inn_candidates (lead_key, name_raw, city, cand_inn, cand_name, cand_city, why, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead_key,
                name,
                city,
                cand.get("inn"),
                cand.get("name_raw"),
                cand.get("city"),
                f"совпадение имени, {cand.get('score', 0)} баллов",
                now,
            ),
        )
    conn.commit()
