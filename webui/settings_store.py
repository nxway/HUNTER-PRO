"""
webui/settings_store.py — чтение/запись settings.json (настройки, кроме
секретов) и .env (секреты, отдельно и по-другому).

Секреты в .env маскируются при отдаче в форму (mask()) — реальные значения
никогда не уходят обратно в HTML, только факт "заполнено" и последние
символы для узнавания. Пустое поле в форме при сохранении = "не менять",
а не "стереть ключ" — иначе случайный сабмит формы без изменений потушит
уже работающий ai/client.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import config

ENV_KEYS = ["OPENROUTER_API_KEY", "FNS_API_KEY", "FSSP_API_TOKEN", "FEDRESURS_API_TOKEN"]

ENV_HINTS = {
    "OPENROUTER_API_KEY": "нужен для ai/client.py (этап 7)",
    "FNS_API_KEY": "пока не используется — enrich/fns_bulk.py работает с файлами",
    "FSSP_API_TOKEN": "для enrich/risk.py — проверка через ФССП",
    "FEDRESURS_API_TOKEN": "для enrich/risk.py — проверка через Федресурс",
}


def load_settings() -> dict:
    """Текущие эффективные настройки: сперва то, что уже подхватил config.py
    при импорте, затем поверх — свежее содержимое settings.json (на случай,
    если его сохранили после старта панели, без перезапуска процесса)."""
    result: dict = {}
    for json_key, var_name in config._EDITABLE_FIELDS.items():
        result[json_key] = getattr(config, var_name)
    result["request_delay_min"] = config.REQUEST_DELAY[0]
    result["request_delay_max"] = config.REQUEST_DELAY[1]

    if config.SETTINGS_PATH.exists():
        try:
            saved = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
            result.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
    return result


def save_settings(new_values: dict) -> None:
    current = load_settings()
    current.update(new_values)
    config.SETTINGS_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _env_path() -> Path:
    return config.BASE_DIR / ".env"


def load_env_raw() -> dict:
    """Реальные значения из .env — только для внутреннего чтения (не
    отдавать в шаблон напрямую)."""
    path = _env_path()
    values: dict = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, val = stripped.partition("=")
            values[key.strip()] = val.strip()
    return values


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def load_env_status() -> dict:
    """{ключ: {"set": bool, "masked": str, "hint": str}} — безопасно для шаблона."""
    raw = load_env_raw()
    return {
        key: {"set": bool(raw.get(key)), "masked": mask(raw.get(key, "")), "hint": ENV_HINTS.get(key, "")}
        for key in ENV_KEYS
    }


def save_env_values(updates: dict) -> None:
    """updates: {ключ: новое значение}. Пустая строка = не менять текущее."""
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    found = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates and updates[key]:
                new_lines.append(f"{key}={updates[key]}")
                found.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if value and key not in found:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
