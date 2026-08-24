"""
enrich/inn.py — работа с ИНН, которая не требует интернета.

Часть XV.1 ТЗ: проверка контрольной суммы — это фильтр опечаток и мусора
из парсинга источников, а не проверка того, что фирма существует.
Всё, что не проходит validate(), в базу не попадает (см. db.ingest).
"""
from __future__ import annotations

_WEIGHTS_10 = [2, 4, 10, 3, 5, 9, 4, 6, 8]
_WEIGHTS_12_D11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
_WEIGHTS_12_D12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]


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
