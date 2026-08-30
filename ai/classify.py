"""
ai/classify.py — классификация компаний без кода продукции через
ai/client.py. Этап 7 ТЗ.

Кэш — главная экономия (VI.5 ТЗ): компания классифицируется один раз,
условие ai_checked_at IS NULL в выборке гарантирует это. Большинству
компаний ИИ вообще не нужен: если product_code уже известен (из декларации,
7.4 ТЗ), "что возит" заполняется таблицей соответствия бесплатно — сюда
попадают только те, у кого декларации нет, а есть в лучшем случае сайт.

--eval (VI.3 ТЗ): не выбираем модель заранее, сравниваем на своих данных.
Разметь 30 компаний руками (csv: inn,verdict — ships|no), прогони через
несколько моделей-кандидатов, смотри на точность/цену/долю сломанного JSON.

Запуск как отдельный модуль:
    python -m ai.classify --pending --limit 200
    python -m ai.classify --eval --labels my_labels.csv --models "model-a,model-b"
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from typing import Optional

import config
import db
from ai.client import ask
from ai.prompts import CLASSIFY_SYSTEM, classify_user_prompt


def _live_examples(conn, limit_per_side: int = 5) -> list[dict]:
    """Живые примеры из твоих исходов звонков — в промпт классификации
    (7.5 ТЗ): "дал заявку" -> ships, "не тот профиль" -> no."""
    rows = conn.execute(
        """
        SELECT c.name, t.result, c.note
        FROM touches t
        JOIN companies c ON c.inn = t.inn
        WHERE t.result IN ('дал заявку', 'не тот профиль')
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (limit_per_side * 2,),
    ).fetchall()
    return [
        {"name": r["name"], "verdict": "ships" if r["result"] == "дал заявку" else "no", "note": r["note"]}
        for r in rows
    ]


def _site_text_for(site: Optional[str]) -> Optional[str]:
    if not site:
        return None
    try:
        from enrich.site import fetch_site_text

        return fetch_site_text(site).text
    except Exception:  # noqa: BLE001 — сайт недоступен, классифицируем по тому, что есть (имя/город)
        return None


def pending_candidates(conn, limit: int) -> list:
    """Запрос из раздела 6.5 ТЗ — буквально, это и есть кэш-правило."""
    return conn.execute(
        """
        SELECT inn, name, city, site FROM companies c
        WHERE c.legal_status = 'active'
          AND c.ai_checked_at IS NULL
          AND c.product_code IS NULL
          AND (c.revenue IS NULL OR c.revenue > 30000000)
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def classify_pending(conn, limit: int = 200) -> dict:
    """Классифицирует кандидатов, пишет ai_verdict/ai_confidence/ai_reason/
    ai_cargo_guess/ai_checked_at. Не трогает ручные поля (это не enrich,
    но и здесь нет причин лезть в my_status/note — только ai_* колонки)."""
    rows = pending_candidates(conn, limit)
    examples = _live_examples(conn)
    stats = {"checked": 0, "ships": 0, "no": 0, "unclear": 0}

    for row in rows:
        site_text = _site_text_for(row["site"])
        user_prompt = classify_user_prompt(row["name"], row["city"], site_text, examples)
        result = ask(
            task="classify",
            system=CLASSIFY_SYSTEM,
            user=user_prompt,
            model=config.MODEL_CHEAP,
            max_tokens=150,
            inn=row["inn"],
        )
        verdict = result.get("verdict", "unclear")
        conn.execute(
            """
            UPDATE companies
            SET ai_verdict = ?, ai_confidence = ?, ai_reason = ?, ai_cargo_guess = ?, ai_checked_at = ?
            WHERE inn = ?
            """,
            (
                verdict,
                result.get("confidence"),
                result.get("reason"),
                result.get("cargo"),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                row["inn"],
            ),
        )
        stats["checked"] += 1
        stats[verdict] = stats.get(verdict, 0) + 1

    conn.commit()
    return stats


def run_eval(conn, labels_path: str, models: list[str]) -> None:
    """Раздел 6.3 ТЗ: сравнение моделей-кандидатов на своей ручной разметке.
    labels_path — csv с колонками inn,verdict (ships|no), компании должны
    уже быть в базе (собери их обычным прогоном заранее)."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    with open(labels_path, encoding="utf-8-sig") as f:
        labeled = list(csv.DictReader(f))
    if not labeled:
        console.print(f"[yellow]ai.classify --eval: файл {labels_path} пуст[/yellow]")
        return

    examples = _live_examples(conn)
    table = Table(title="ai.classify --eval")
    table.add_column("модель")
    table.add_column("совпадений")
    table.add_column("средняя цена")
    table.add_column("сломанный JSON")

    for model in models:
        correct = broken = considered = 0
        total_cost = 0.0
        for row in labeled:
            company = conn.execute(
                "SELECT name, city, site FROM companies WHERE inn = ?", (row["inn"],)
            ).fetchone()
            if not company:
                console.print(f"[yellow]--eval: ИНН {row['inn']} не найден в базе, пропуск[/yellow]")
                continue

            site_text = _site_text_for(company["site"])
            user_prompt = classify_user_prompt(company["name"], company["city"], site_text, examples)
            result = ask(
                task="classify_eval",
                system=CLASSIFY_SYSTEM,
                user=user_prompt,
                model=model,
                max_tokens=150,
                inn=row["inn"],
            )
            considered += 1
            if result.get("reason") == "модель вернула не-JSON":
                broken += 1
            if result.get("verdict") == row["verdict"]:
                correct += 1

            cost_row = conn.execute(
                "SELECT cost_usd FROM ai_usage WHERE task = 'classify_eval' AND inn = ? ORDER BY id DESC LIMIT 1",
                (row["inn"],),
            ).fetchone()
            total_cost += (cost_row["cost_usd"] or 0) if cost_row else 0

        avg_cost = total_cost / considered if considered else 0
        table.add_row(model, f"{correct}/{considered}", f"${avg_cost:.4f}", str(broken))

    console.print(table)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Классификация компаний через ИИ")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pending", action="store_true", help="классифицировать кандидатов из очереди")
    group.add_argument("--eval", action="store_true", help="сравнить модели-кандидаты на своей разметке")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--labels", help="csv с колонками inn,verdict — для --eval")
    parser.add_argument("--models", help="через запятую — для --eval")
    args = parser.parse_args()

    from rich.console import Console

    console = Console()
    conn = db.init_db()

    if args.pending:
        if not config.MODEL_CHEAP:
            console.print("[red]ai.classify: config.MODEL_CHEAP не задан — сначала прогони --eval[/red]")
            raise SystemExit(1)
        stats = classify_pending(conn, args.limit)
        console.print(
            f"ai.classify: проверено {stats['checked']} · ships={stats.get('ships', 0)} "
            f"· no={stats.get('no', 0)} · unclear={stats.get('unclear', 0)}"
        )
    else:
        if not args.labels or not args.models:
            console.print("[red]ai.classify --eval: нужны --labels и --models[/red]")
            raise SystemExit(1)
        run_eval(conn, args.labels, [m.strip() for m in args.models.split(",") if m.strip()])

    conn.close()


if __name__ == "__main__":
    _main()
