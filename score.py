"""
score.py — раскладка компаний по трём корзинам (раздел 7.1-7.3 ТЗ). Обычный
SQL, без ИИ. Пересчитывается по команде `python score.py`.

Главное правило (7.1): в отсев (красная) уходят только по явному признаку
мусора. Нехватка данных — не признак мусора, это жёлтая корзина.

На этом этапе у базы ещё нет ai_verdict (появится на этапе 7) и
legal_status (появится на этапе 4, набор ФНС) — оба признака красной
корзины уже заложены в запрос ниже, но реально сработают только когда эти
поля начнут заполняться. Раньше времени изобретать других причин для
красной корзины не нужно: пока явных доказательств мусора взяться неоткуда,
красная корзина остаётся пустой, и это правильное поведение, а не баг.

Запуск: python score.py
"""
from __future__ import annotations

import sqlite3

import db


def assign_buckets(conn: sqlite3.Connection) -> dict[str, int]:
    conn.execute(
        """
        UPDATE companies
        SET bucket = 'red',
            bucket_reason = CASE
                WHEN ai_verdict = 'no' THEN 'ИИ: не возит груз'
                WHEN legal_status IN ('dead', 'liquidating') THEN 'фирма не действует'
            END
        WHERE ai_verdict = 'no' OR legal_status IN ('dead', 'liquidating')
        """
    )
    conn.execute(
        """
        UPDATE companies
        SET bucket = 'green', bucket_reason = NULL
        WHERE bucket != 'red' AND phone IS NOT NULL AND phone != ''
        """
    )
    conn.execute(
        """
        UPDATE companies
        SET bucket = 'yellow', bucket_reason = NULL
        WHERE bucket != 'red' AND (phone IS NULL OR phone = '')
        """
    )
    conn.commit()

    counts: dict[str, int] = {"green": 0, "yellow": 0, "red": 0}
    for row in conn.execute("SELECT bucket, COUNT(*) AS n FROM companies GROUP BY bucket"):
        counts[row["bucket"]] = row["n"]
    return counts


def main() -> None:
    from rich.console import Console

    console = Console()
    conn = db.init_db()
    counts = assign_buckets(conn)
    conn.close()
    console.print(
        f"score: зелёных {counts['green']} · жёлтых {counts['yellow']} · красных {counts['red']}"
    )


if __name__ == "__main__":
    main()
