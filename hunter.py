"""
hunter.py — единая точка входа: run | export (разделы VIII, X ТЗ).

Логики сбора/обогащения/выгрузки внутри себя не держит — только вызывает по
порядку sources/*.py, enrich/*.py, score.py, export.py, каждый из которых
по-прежнему запускается и сам по себе (1.1.7 ТЗ). Один источник, упавший
целиком, не должен останавливать остальные и весь прогон (раздел X ТЗ).

Запуск:
    python hunter.py run       # собрать + обогатить телефоном + разложить по корзинам
    python hunter.py export    # выгрузить .xlsx новых компаний
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone


def cmd_run(args: argparse.Namespace) -> None:
    import config
    import db
    import inbox_import
    import score
    from enrich import fns_bulk, risk
    from enrich.site import enrich_missing_phones
    from rich.console import Console
    from sources import registry

    console = Console()
    started_at = time.time()
    run_start_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = db.init_db()

    inbox_totals = inbox_import.import_inbox(conn)
    if inbox_totals["files"]:
        console.print(
            f"inbox_import: файлов {inbox_totals['files']} · обработано {inbox_totals['processed']} "
            f"· понижено по массовому вердикту {inbox_totals['mass_downgraded']}"
        )

    sources_map = registry.discover()
    if not sources_map:
        console.print("[yellow]hunter run: не найдено ни одного источника в sources/[/yellow]")

    total_collected = total_new_companies = 0
    for key, module in sources_map.items():
        settings = dict(module.SPEC.default_settings)
        collected = new_companies = new_signals = 0
        try:
            for lead in module.collect(settings):
                result = db.ingest(conn, lead)
                collected += 1
                new_companies += int(result.company_new)
                new_signals += int(result.signal_new)
        except Exception as exc:  # noqa: BLE001 — сбой одного источника не должен ронять прогон (раздел X ТЗ)
            console.print(f"[red]{key}: сбой сбора — {exc}[/red]")
            continue
        console.print(f"{key}: собрано {collected} · новых компаний {new_companies} · новых сигналов {new_signals}")
        total_collected += collected
        total_new_companies += new_companies

    fns_updated = fns_bulk.enrich_all()
    if fns_updated:
        console.print(f"fns_bulk: обогащено {fns_updated} компаний из registry")

    stats = enrich_missing_phones(conn, args.enrich_limit, console=console)
    console.print(
        f"обогащение телефона: проверено {stats['checked']} · нашли на сайте {stats['via_site']} "
        f"· нашли в 2ГИС {stats['via_dgis']} · не нашли {stats['not_found']}"
    )

    # ИИ — раздел IX ТЗ: только если модель выбрана (--eval уже прогнан),
    # --dry-run считает и печатает, но ничего не тратит (IX.2 ТЗ).
    ai_checked = 0
    if config.MODEL_CHEAP:
        from ai.classify import classify_pending, pending_candidates

        pending = pending_candidates(conn, args.ai_limit)
        if args.dry_run:
            console.print(f"[cyan]--dry-run: ИИ нужен {len(pending)} компаниям, ничего не потрачено[/cyan]")
        elif pending:
            ai_stats = classify_pending(conn, args.ai_limit)
            ai_checked = ai_stats["checked"]
            console.print(
                f"ai.classify: проверено {ai_checked} · ships={ai_stats.get('ships', 0)} "
                f"· no={ai_stats.get('no', 0)} · unclear={ai_stats.get('unclear', 0)}"
            )
    elif args.dry_run:
        console.print("[cyan]--dry-run: config.MODEL_CHEAP не задан, оценка по ИИ недоступна[/cyan]")

    counts = score.assign_buckets(conn)
    console.print(f"score: зелёных {counts['green']} · жёлтых {counts['yellow']} · красных {counts['red']}")

    if not args.dry_run:
        risk_counts = risk.check_pending(conn, args.risk_limit)
        if sum(risk_counts.values()):
            console.print(
                f"risk: ok={risk_counts['ok']} · watch={risk_counts['watch']} · stop={risk_counts['stop']}"
            )

    ai_spent = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM ai_usage WHERE ts >= ?", (run_start_ts,)
    ).fetchone()["total"]
    conn.close()

    elapsed_min = (time.time() - started_at) / 60
    console.print(
        f"Собрано {total_collected} · новых {total_new_companies} · в ИИ ушло {ai_checked} "
        f"· потрачено ${ai_spent:.2f} · время {elapsed_min:.0f} мин"
    )


def cmd_export(args: argparse.Namespace) -> None:
    import os
    import sys

    import db
    import export
    from rich.console import Console

    console = Console()
    conn = db.init_db()
    path = export.export_leads(conn)
    conn.close()
    if not path:
        console.print("export: новых компаний для выгрузки нет")
        return

    console.print(f"export: выгружено {path}")
    # 8.1 ТЗ: выгрузка сразу открывает файл — это и есть "кнопка".
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 — открытие своего только что созданного файла
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except OSError as exc:
        console.print(f"[yellow]не удалось открыть файл автоматически: {exc}[/yellow]")


def main() -> None:
    parser = argparse.ArgumentParser(description="HUNTER-PRO")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="собрать + обогатить телефоном + разложить по корзинам")
    p_run.add_argument("--enrich-limit", type=int, default=100)
    p_run.add_argument("--ai-limit", type=int, default=200)
    p_run.add_argument("--risk-limit", type=int, default=200)
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="ничего не тратит на ИИ и не проверяет риск — только считает и печатает (IX.2 ТЗ)",
    )
    p_run.set_defaults(func=cmd_run)

    p_export = sub.add_parser("export", help="выгрузить .xlsx новых компаний")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
