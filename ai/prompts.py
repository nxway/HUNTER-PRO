"""
ai/prompts.py — тексты промптов отдельно от кода (раздел VI.4 ТЗ).

CLASSIFY_SYSTEM — литерально текст из раздела 6.4 ТЗ. "cargo" в ответе
должен возвращаться в той же форме, что и cargo_map (7.4 ТЗ) — иначе в
колонке "Что возит" окажутся две разные системы описаний.
"""
from __future__ import annotations

from typing import Optional

CLASSIFY_SYSTEM = """Ты аналитик транспортной компании. По данным о фирме определи, есть ли у неё
регулярные грузы для перевозки автотранспортом по России.

Признаки «да»: собственное производство, оптовые отгрузки, склад, дилерская
сеть, упоминания отгрузки/самовывоза/доставки по РФ, прайс на товар.
Признаки «нет»: услуги, IT, консалтинг, розничная точка без склада,
транспортная компания (это конкурент, не клиент), госучреждение.

Ответь ТОЛЬКО объектом JSON, без пояснений и разметки:
{"verdict":"ships|no|unclear","confidence":0-100,
 "cargo":"краткое описание груза и типа кузова","reason":"одна фраза"}"""


def classify_user_prompt(
    name: str,
    city: Optional[str] = None,
    site_text: Optional[str] = None,
    examples: Optional[list[dict]] = None,
) -> str:
    """Собирает пользовательское сообщение для классификации одной компании.

    examples — живые примеры из твоих собственных исходов звонков (7.5 ТЗ):
    список {"name": ..., "verdict": "ships"|"no", "note": ...}. Никакого
    дообучения — просто текст в промпте, обрезка входа делает ai/client.py
    (MAX_AI_INPUT_CHARS), не здесь."""
    parts = [f"Компания: {name}"]
    if city:
        parts.append(f"Город: {city}")
    if site_text:
        parts.append(f"Текст сайта: {site_text}")
    if examples:
        parts.append("Примеры из моей практики (для ориентира, не для копирования):")
        for ex in examples:
            note = f" — {ex['note']}" if ex.get("note") else ""
            parts.append(f"- {ex['name']}: {ex['verdict']}{note}")
    return "\n".join(parts)
