"""
webui/app.py — локальная веб-панель HUNTER-PRO. Поднимается по требованию
(см. "HUNTER — панель.bat"), не висит фоновой службой: закрыли окно .bat —
сервер погас. Слушает только 127.0.0.1 — наружу из машины не торчит.

Панель не заменяет и не дублирует основной пайплайн (hunter.py run/export,
sources/*.py и т.п.) — она вызывает их же самые модули: кнопки "Собрать"/
"Выгрузить" запускают hunter.py тем же интерпретатором подпроцессом
(webui/run_manager.py), список компаний читает ту же leads.sqlite.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, url_for

import config
import db
from webui import data, run_manager, settings_store

app = Flask(__name__)


def get_conn() -> sqlite3.Connection:
    return db.init_db()


@app.route("/")
def dashboard():
    conn = get_conn()
    try:
        counts = data.bucket_counts(conn)
        regions = data.distinct_regions(conn)
        page = max(int(request.args.get("page", 1) or 1), 1)
        filters = {
            "bucket": request.args.get("bucket", ""),
            "my_status": request.args.get("my_status", ""),
            "region": request.args.get("region", ""),
            "q": request.args.get("q", ""),
        }
        companies, total = data.query_companies(conn, page=page, **filters)
    finally:
        conn.close()

    total_pages = max((total + data.PER_PAGE - 1) // data.PER_PAGE, 1)
    return render_template(
        "dashboard.html",
        counts=counts,
        regions=regions,
        companies=companies,
        total=total,
        page=page,
        total_pages=total_pages,
        filters=filters,
        run_status=run_manager.status(),
    )


@app.route("/run", methods=["POST"])
def trigger_run():
    dry_run = request.form.get("dry_run") == "on"
    run_manager.start("run", dry_run=dry_run)
    return redirect(url_for("dashboard"))


@app.route("/export", methods=["POST"])
def trigger_export():
    run_manager.start("export")
    return redirect(url_for("dashboard"))


@app.route("/api/status")
def api_status():
    return jsonify(run_manager.status())


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    message = None
    if request.method == "POST":
        form = request.form
        new_values = {
            "target_regions": _parse_int_list(form.get("target_regions", "")),
            "my_lanes": _parse_str_list(form.get("my_lanes", "")),
            "model_cheap": form.get("model_cheap", "").strip(),
            "model_fallback": form.get("model_fallback", "").strip(),
            "max_ai_calls_per_run": _parse_int(form.get("max_ai_calls_per_run"), config.MAX_AI_CALLS_PER_RUN),
            "max_ai_spend_per_day_usd": _parse_float(
                form.get("max_ai_spend_per_day_usd"), config.MAX_AI_SPEND_PER_DAY_USD
            ),
            "reexport_after_days": _parse_int(form.get("reexport_after_days"), config.REEXPORT_AFTER_DAYS),
            "request_delay_min": _parse_float(form.get("request_delay_min"), config.REQUEST_DELAY[0]),
            "request_delay_max": _parse_float(form.get("request_delay_max"), config.REQUEST_DELAY[1]),
            "http_timeout": _parse_int(form.get("http_timeout"), config.HTTP_TIMEOUT),
            "user_agent": form.get("user_agent", "").strip(),
            "signal_weights": {
                "size_ok": _parse_int(form.get("weight_size_ok"), 0),
                "vat_osno": _parse_int(form.get("weight_vat_osno"), 0),
                "my_lane": _parse_int(form.get("weight_my_lane"), 0),
                "direct_phone": _parse_int(form.get("weight_direct_phone"), 0),
                "cargo_known": _parse_int(form.get("weight_cargo_known"), 0),
            },
        }
        settings_store.save_settings(new_values)

        env_updates = {key: form.get(f"env_{key}", "").strip() for key in settings_store.ENV_KEYS}
        settings_store.save_env_values(env_updates)

        message = "Сохранено. Подхватится при следующем запуске «Собрать»/«Выгрузить»."

    return render_template(
        "settings.html",
        settings=settings_store.load_settings(),
        env_status=settings_store.load_env_status(),
        message=message,
    )


@app.route("/logs")
def logs_page():
    return render_template("logs.html")


@app.route("/api/logs")
def api_logs():
    log_path = config.LOG_DIR / "hunter.log"
    checked_at = datetime.now().strftime("%H:%M:%S")
    if not log_path.exists():
        return jsonify({"lines": [], "exists": False, "checked_at": checked_at})
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-300:]
    return jsonify({"lines": tail, "exists": True, "checked_at": checked_at})


def _parse_int_list(raw: str) -> list[int]:
    return [int(p.strip()) for p in raw.split(",") if p.strip().isdigit()]


def _parse_str_list(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_int(raw, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _parse_float(raw, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def main() -> None:
    import webbrowser
    from threading import Timer

    url = "http://127.0.0.1:5000/"
    Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    main()
