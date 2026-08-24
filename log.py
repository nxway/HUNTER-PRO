"""
log.py — единый логгер проекта (раздел 12.1 ТЗ): один файл logs/hunter.log,
ротация по дням, человекочитаемый вывод в консоль через rich.

Используется в hunter.py run — это единственная точка, которая работает
без присмотра по ночам (раздел X ТЗ), и единственное место, где падение в
2 часа ночи узнаётся не "никак", а по файлу. Обязательные точки лога по
12.1 ТЗ: старт/конец сбора, сколько собрано и сколько новых, каждая
сетевая ошибка с URL, каждый вызов модели с ценой — расставлены в
hunter.py cmd_run.

Отдельные модули (sources/*.py, enrich/*.py и т.п.), запущенные вручную
как `python -m sources.fsa_declarations`, по-прежнему используют свой
rich.console.Console() для интерактивного вывода — им файл не нужен, это
разовый ручной запуск, а не ночной прогон без присмотра.
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

from rich.logging import RichHandler

import config

_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hunter")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    file_handler = TimedRotatingFileHandler(
        config.LOG_DIR / "hunter.log", when="midnight", backupCount=30, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(file_handler)

    console_handler = RichHandler(show_path=False, show_level=False, markup=True)
    logger.addHandler(console_handler)

    _logger = logger
    return logger


class LoggerConsole:
    """Мини-адаптер: даёт логгеру интерфейс .print(), которого ждут модули,
    написанные под rich.Console (например, enrich.site.enrich_missing_phones).
    Так их предупреждения о сетевых сбоях тоже попадают в logs/hunter.log,
    без переделки уже написанного и протестированного кода этих модулей."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def print(self, message: str) -> None:
        self._logger.info(message)


def as_console(logger: logging.Logger) -> LoggerConsole:
    return LoggerConsole(logger)
