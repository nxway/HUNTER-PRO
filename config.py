"""
config.py — все настройки и веса проекта, в одном месте.

Правило: ни одного «магического» числа в остальном коде. Все пороги, веса,
лимиты и пути — здесь, с комментариями по-русски, чтобы через месяц их можно
было крутить руками, не разыскивая по файлам.

Значения ниже — умолчания. Если рядом с проектом лежит settings.json
(пишет его webui/, панель настроек), значения из него перекрывают умолчания
при импорте — см. _apply_overrides() в конце файла. Ручная правка этого
файла по-прежнему работает как раньше — settings.json нужен только тем, кто
предпочитает править из браузера.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Пути проекта ---
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "leads.sqlite"
RAW_DIR = BASE_DIR / "raw"
LOG_DIR = BASE_DIR / "logs"
SETTINGS_PATH = BASE_DIR / "settings.json"

# --- Секреты (значения читаются из .env, сюда не вписывать) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
FNS_API_KEY = os.getenv("FNS_API_KEY", "")
FSSP_API_TOKEN = os.getenv("FSSP_API_TOKEN", "")          # раздел 16.2 ТЗ, api-ip.fssp.gov.ru
FEDRESURS_API_TOKEN = os.getenv("FEDRESURS_API_TOKEN", "")  # раздел 16.2 ТЗ, fedresurs.ru

# --- Регионы работы (коды регионов РФ, первые две цифры ИНН) ---
TARGET_REGIONS = [77, 50, 78, 47, 66, 52]  # Москва, МО, СПб, ЛО, Свердл., Нижег.

# --- Мои рабочие плечи: города, куда реально вожу ---
MY_LANES = ["Москва", "Санкт-Петербург", "Екатеринбург", "Новосибирск"]

# --- ОКВЭД: кто в принципе может дать груз ---
OKVED_PREFIXES = [
    "10", "11", "13", "14", "15", "16", "17",   # пищевое, текстиль, дерево, бумага
    "20", "21", "22", "23", "24", "25",          # химия, пластик, стройматериалы, металл
    "26", "27", "28", "29", "30", "31", "32",    # техника, мебель, прочее производство
    "46",                                        # оптовая торговля
]

# --- Модели ИИ: имена НЕ зашивать в код, только сюда (заполняется на этапе 7) ---
MODEL_CHEAP = ""      # массовая классификация — выбрать через ai/classify.py --eval
MODEL_FALLBACK = ""   # если основная недоступна

# --- Резервный расчёт цены за 1000 токенов, если OpenRouter не вернул usage.cost ---
# Заглушка — впиши реальные цены для выбранных моделей после --eval (VI.1 ТЗ).
PRICE_TABLE: dict[str, dict[str, float]] = {
    # "имя-модели:floor": {"in": 0.0002, "out": 0.0006},
}

# --- Предохранители ---
MAX_AI_CALLS_PER_RUN = 300
MAX_AI_SPEND_PER_DAY_USD = 0.50    # жёсткий стоп, не предупреждение
MAX_AI_INPUT_CHARS = {"classify": 2500}
REEXPORT_AFTER_DAYS = 180          # когда компания может всплыть повторно
REQUEST_DELAY = (1.0, 3.0)         # случайная пауза между запросами, сек
HTTP_TIMEOUT = 20
USER_AGENT = "HunterPro/1.0 (+your-email@example.com)"  # впиши свою контактную почту

# --- Веса скоринга «Подходит», 0-100, см. раздел 7.6 ТЗ. Заглушка — откалибровать. ---
SIGNAL_WEIGHTS = {
    "size_ok": 25,       # размер бизнеса в рабочем диапазоне
    "vat_osno": 20,      # компания на ОСНО
    "my_lane": 25,       # город на моих рабочих плечах
    "direct_phone": 20,  # есть прямой телефон
    "cargo_known": 10,   # известен род груза
}

# --- Поля, доступные для правки через settings.json / панель настроек ---
# Ключ settings.json -> имя переменной модуля. Секреты (.env) сюда
# намеренно не входят — панель правит их отдельно, напрямую в .env.
_EDITABLE_FIELDS = {
    "target_regions": "TARGET_REGIONS",
    "my_lanes": "MY_LANES",
    "model_cheap": "MODEL_CHEAP",
    "model_fallback": "MODEL_FALLBACK",
    "max_ai_calls_per_run": "MAX_AI_CALLS_PER_RUN",
    "max_ai_spend_per_day_usd": "MAX_AI_SPEND_PER_DAY_USD",
    "reexport_after_days": "REEXPORT_AFTER_DAYS",
    "http_timeout": "HTTP_TIMEOUT",
    "user_agent": "USER_AGENT",
    "signal_weights": "SIGNAL_WEIGHTS",
}


def _apply_overrides() -> None:
    """Перекрывает умолчания значениями из settings.json, если файл есть.
    Вызывается один раз при импорте модуля. REQUEST_DELAY хранится в JSON
    как пара request_delay_min/request_delay_max — кортежи JSON не умеет."""
    if not SETTINGS_PATH.exists():
        return
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    module_globals = globals()
    for json_key, var_name in _EDITABLE_FIELDS.items():
        if json_key in data:
            module_globals[var_name] = data[json_key]

    if "request_delay_min" in data or "request_delay_max" in data:
        module_globals["REQUEST_DELAY"] = (
            data.get("request_delay_min", REQUEST_DELAY[0]),
            data.get("request_delay_max", REQUEST_DELAY[1]),
        )


_apply_overrides()
