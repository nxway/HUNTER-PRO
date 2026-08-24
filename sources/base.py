"""
sources/base.py — общий каркас для всех источников: RawLead, SourceSpec и
сохранение сырья. Не сборщик — сюда не добавляется логика конкретных сайтов.
"""
from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import config


@dataclass
class RawLead:
    """То, что источник отдаёт наружу. Контракт 5.1 ТЗ."""

    name: str                 # название как есть
    source: str                # 'fsa' | 'expo' | 'dgis' | 'zakupki'
    signal_type: str
    signal_date: str          # ISO
    dedup_key: str             # обязателен, см. signals.dedup_key
    url: Optional[str] = None
    summary: Optional[str] = None       # что за товар, если источник знает
    product_code: Optional[str] = None  # код продукции → cargo_map
    city: Optional[str] = None
    site: Optional[str] = None
    phone: Optional[str] = None
    inn: Optional[str] = None           # если источник дал — счастье
    raw_path: Optional[str] = None


@dataclass
class SourceSpec:
    """Паспорт источника. Контракт 14.1 ТЗ."""

    key: str                   # уникальный, латиницей
    title: str
    kind: str                  # 'base' — справочник, 'event' — событие
    signal_type: str           # что кладём в signals.type
    gives_inn: bool = False
    needs_browser: bool = False
    default_settings: dict = field(default_factory=dict)
    setting_hints: dict = field(default_factory=dict)


def save_raw(source_key: str, name: str, content: bytes | str, day: Optional[date] = None) -> str:
    """Сохраняет сырьё в raw/<дата>/<source_key>_<name>.gz, возвращает путь
    относительно корня проекта — только его и кладём в raw_path в базе
    (4.3.3 ТЗ: сырой HTML в базу не кладём)."""
    day = day or date.today()
    out_dir = config.RAW_DIR / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{source_key}_{name}.gz"
    data = content.encode("utf-8") if isinstance(content, str) else content
    with gzip.open(path, "wb") as f:
        f.write(data)
    return str(path.relative_to(config.BASE_DIR))
