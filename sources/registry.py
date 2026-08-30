"""
sources/registry.py — автопоиск источников в папке sources/.

Ни одного источника руками в список не дописываем: если у модуля есть
SPEC и collect(), он подхватывается сам (14.2 ТЗ).
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType

import sources

_EXCLUDED = {"base", "registry", "template", "__init__"}


def discover() -> dict[str, ModuleType]:
    """Сканирует папку sources/, импортирует каждый модуль с SPEC и collect(),
    возвращает {SPEC.key: модуль}."""
    found: dict[str, ModuleType] = {}
    pkg_dir = Path(sources.__file__).parent
    for info in pkgutil.iter_modules([str(pkg_dir)]):
        if info.name in _EXCLUDED or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"sources.{info.name}")
        spec = getattr(module, "SPEC", None)
        if spec is None or not hasattr(module, "collect"):
            continue
        found[spec.key] = module
    return found


def get(key: str) -> ModuleType:
    found = discover()
    if key not in found:
        raise KeyError(f"источник '{key}' не найден среди {sorted(found)}")
    return found[key]
