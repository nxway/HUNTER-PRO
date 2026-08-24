"""
webui/data.py — запросы к leads.sqlite для панели. Только чтение: панель
не редактирует компании напрямую (это уже делает inbox_import.py через
Excel — заводить второй, конфликтующий способ править те же данные не
нужно, отсюда только SELECT).
"""
from __future__ import annotations

import sqlite3

_BUCKET_LABELS = {"green": "зелёная", "yellow": "жёлтая", "red": "красная"}
_STATUS_LABELS = {
    "new": "новая",
    "exported": "выгружена",
    "called": "звонили",
    "client": "клиент",
    "refused": "отказ",
}

PER_PAGE = 50


def bucket_counts(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT bucket, COUNT(*) AS n FROM companies GROUP BY bucket").fetchall()
    counts = {"green": 0, "yellow": 0, "red": 0}
    for row in rows:
        counts[row["bucket"]] = row["n"]
    counts["total"] = sum(counts.values())
    return counts


def distinct_regions(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT region_code FROM companies WHERE region_code IS NOT NULL ORDER BY region_code"
    ).fetchall()
    return [r["region_code"] for r in rows]


def query_companies(
    conn: sqlite3.Connection,
    bucket: str = "",
    my_status: str = "",
    region: str = "",
    q: str = "",
    page: int = 1,
) -> tuple[list[dict], int]:
    where = []
    params: dict = {}
    if bucket:
        where.append("bucket = :bucket")
        params["bucket"] = bucket
    if my_status:
        where.append("my_status = :my_status")
        params["my_status"] = my_status
    if region:
        where.append("region_code = :region")
        params["region"] = region
    if q:
        where.append("(name LIKE :q OR city LIKE :q OR inn LIKE :q)")
        params["q"] = f"%{q}%"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(f"SELECT COUNT(*) FROM companies {where_sql}", params).fetchone()[0]

    page = max(page, 1)
    offset = (page - 1) * PER_PAGE
    rows = conn.execute(
        f"""
        SELECT inn, name, phone, city, cargo, bucket, my_status, region_code, score, created_at
        FROM companies {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": PER_PAGE, "offset": offset},
    ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["bucket_label"] = _BUCKET_LABELS.get(item["bucket"], item["bucket"] or "")
        item["status_label"] = _STATUS_LABELS.get(item["my_status"], item["my_status"] or "")
        items.append(item)

    return items, total
