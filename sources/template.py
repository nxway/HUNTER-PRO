"""
sources/template.py — копируй этот файл при добавлении нового источника
(14.1 ТЗ). Ничего наружу, кроме SPEC и collect(), модулю выставлять не нужно.
"""
from sources.base import RawLead, SourceSpec

SPEC = SourceSpec(
    key="my_source",                 # уникальный, латиницей
    title="Название источника",
    kind="event",                    # 'base' — справочник, 'event' — событие
    signal_type="new_declaration",   # что кладём в signals.type
    gives_inn=True,                  # источник сразу даёт ИНН?
    needs_browser=False,             # нужен ли Playwright
    default_settings={               # можно переопределить в профиле
        "regions": [77, 50],
        "delay_sec": 2.5,
        "limit": 500,
    },
    setting_hints={                  # подписи для лога и отчёта
        "regions": "Коды регионов",
        "delay_sec": "Пауза между запросами, сек",
        "limit": "Максимум записей за прогон",
    },
)


def collect(settings: dict):
    """Единственная обязательная функция. Отдаёт RawLead по одному."""
    raise NotImplementedError("скопируй файл в sources/<имя>.py и реализуй сбор")
    yield RawLead  # pragma: no cover — чтобы функция оставалась генератором
