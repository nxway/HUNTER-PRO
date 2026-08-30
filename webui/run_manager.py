"""
webui/run_manager.py — запуск hunter.py run/export как подпроцесса из
панели, с отслеживанием состояния "выполняется / нет". Один прогон за раз:
повторный запрос, пока предыдущий не закончился, отклоняется, а не
ставится в очередь и не запускается параллельно — параллельный hunter.py
run дважды в одну и ту же базу не задумывался.

Подпроцесс запускается тем же интерпретатором (sys.executable), которым
запущена сама панель — если панель стартовала из .venv, дочерний hunter.py
тоже возьмёт .venv, а не системный Python.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Optional

import config

_lock = threading.Lock()
_state = {
    "running": False,
    "kind": None,
    "returncode": None,
    "started_at": None,
    "finished_at": None,
}


def status() -> dict:
    with _lock:
        return dict(_state)


def is_running() -> bool:
    with _lock:
        return _state["running"]


def _run_in_thread(args: list[str], kind: str) -> None:
    process = subprocess.Popen(
        [sys.executable, str(config.BASE_DIR / "hunter.py"), *args],
        cwd=str(config.BASE_DIR),
    )
    returncode = process.wait()
    with _lock:
        _state["running"] = False
        _state["returncode"] = returncode
        _state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")


def start(kind: str, dry_run: bool = False) -> bool:
    """kind: 'run' | 'export'. Возвращает False, если уже что-то выполняется."""
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
        _state["kind"] = kind
        _state["returncode"] = None
        _state["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _state["finished_at"] = None

    args = [kind]
    if kind == "run" and dry_run:
        args.append("--dry-run")

    threading.Thread(target=_run_in_thread, args=(args, kind), daemon=True).start()
    return True
