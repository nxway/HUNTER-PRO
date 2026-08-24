"""
db.py — подключение к SQLite, миграции, единая точка записи данных из
источников.

Правило слоёв (2.1 ТЗ): sources/*.py не пишут в companies напрямую — они
отдают RawLead, а ingest() здесь решает, как это лечь в базу.

Правило 4.3 ТЗ, обязательное к соблюдению в каждой миграции UPSERT-ом:
  1. Только UPSERT, никогда INSERT без ON CONFLICT — иначе повторный запуск
     наплодит дублей.
  2. Обогащение никогда не трогает ручные поля (my_status, note, exported_at,
     payment_term, otkat и т.п.) — обновляются только «источниковые» колонки.
  3. Сырой HTML в базу не кладём — только путь в raw/.

Примечание к миграции 1: полный DDL из раздела 4.1 ТЗ содержит индекс
`idx_comp_export ON companies(exported_at)`, а колонка `exported_at`
появляется только на этапе 3 (раздел 8.3, ALTER TABLE). Индекс на
несуществующую колонку SQLite не создаст. Поэтому здесь он отложен —
добавится вместе с колонкой в миграции 2 на этапе 3, а не изобретается
заранее.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import config
from enrich.inn import region_of, validate as validate_inn
from sources.base import RawLead

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS companies (
            inn             TEXT PRIMARY KEY,
            ogrn            TEXT,
            name            TEXT NOT NULL,
            short_name      TEXT,
            okved           TEXT,
            product_code    TEXT,
            cargo           TEXT,
            body_type       TEXT,
            revenue         INTEGER,
            revenue_year    INTEGER,
            employees       INTEGER,
            vat_guess       TEXT,
            legal_status    TEXT,
            region_code     INTEGER,
            city            TEXT,
            address         TEXT,
            warehouse_city  TEXT,
            site            TEXT,
            phone           TEXT,
            phone_extra     TEXT,
            email           TEXT,
            contact_person  TEXT,
            contact_role    TEXT,
            score           REAL DEFAULT 0,
            ai_verdict      TEXT,
            ai_confidence   INTEGER,
            ai_reason       TEXT,
            ai_cargo_guess  TEXT,
            ai_checked_at   TEXT,
            bucket          TEXT DEFAULT 'yellow',
            bucket_reason   TEXT,
            my_status       TEXT DEFAULT 'new',
            payment_form    TEXT,
            payment_term    TEXT,
            docs_for_payment TEXT,
            otkat           TEXT,
            note            TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            inn         TEXT REFERENCES companies(inn),
            lead_key    TEXT,
            type        TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            url         TEXT,
            summary     TEXT,
            source      TEXT NOT NULL,
            raw_path    TEXT,
            dedup_key   TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            CHECK (inn IS NOT NULL OR lead_key IS NOT NULL)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS touches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            inn         TEXT NOT NULL REFERENCES companies(inn),
            touch_date  TEXT NOT NULL,
            person      TEXT,
            result      TEXT,
            next_step   TEXT,
            next_date   TEXT,
            created_at  TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            model       TEXT NOT NULL,
            task        TEXT NOT NULL,
            inn         TEXT,
            tokens_in   INTEGER,
            tokens_out  INTEGER,
            cost_usd    REAL,
            ok          INTEGER DEFAULT 1
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_signals_inn  ON signals(inn)",
        "CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date)",
        "CREATE INDEX IF NOT EXISTS idx_comp_score   ON companies(score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_comp_status  ON companies(my_status)",
        "CREATE INDEX IF NOT EXISTS idx_comp_bucket  ON companies(bucket)",
        "CREATE INDEX IF NOT EXISTS idx_usage_ts     ON ai_usage(ts)",
    ],
    # Этап 3, раздел 8.3 ТЗ: одну компанию выгружаем один раз.
    2: [
        "ALTER TABLE companies ADD COLUMN exported_at TEXT",
        "ALTER TABLE companies ADD COLUMN export_count INTEGER DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS idx_comp_export ON companies(exported_at)",
    ],
}


def get_connection() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    for version in sorted(MIGRATIONS):
        if version in applied:
            continue
        for statement in MIGRATIONS[version]:
            conn.execute(statement)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()


def init_db() -> sqlite3.Connection:
    conn = get_connection()
    migrate(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class IngestResult:
    company_new: bool
    company_updated: bool
    signal_new: bool


def ingest(conn: sqlite3.Connection, lead: RawLead) -> IngestResult:
    """Кладёт один RawLead в базу.

    ИНН, не прошедший проверку контрольной суммы, в companies не попадает —
    "сомневаешься — не связывай" (15.3 ТЗ). Сигнал в этом случае всё равно
    сохраняется, но привязан к lead_key (используем dedup_key как временный
    ключ — RawLead своего lead_key не несёт по контракту 5.1).
    """
    now = _now()
    inn = lead.inn if lead.inn and validate_inn(lead.inn) else None

    company_new = False
    company_updated = False
    if inn:
        existing = conn.execute(
            "SELECT 1 FROM companies WHERE inn = ?", (inn,)
        ).fetchone()
        company_new = existing is None
        company_updated = not company_new
        conn.execute(
            """
            INSERT INTO companies (inn, name, product_code, city, site, phone,
                                    region_code, legal_status, created_at, updated_at)
            VALUES (:inn, :name, :product_code, :city, :site, :phone,
                    :region_code, 'active', :now, :now)
            ON CONFLICT(inn) DO UPDATE SET
                name         = excluded.name,
                product_code = COALESCE(excluded.product_code, companies.product_code),
                city         = COALESCE(excluded.city, companies.city),
                site         = COALESCE(excluded.site, companies.site),
                phone        = COALESCE(excluded.phone, companies.phone),
                region_code  = COALESCE(companies.region_code, excluded.region_code),
                updated_at   = excluded.updated_at
            """,
            {
                "inn": inn,
                "name": lead.name,
                "product_code": lead.product_code,
                "city": lead.city,
                "site": lead.site,
                "phone": lead.phone,
                "region_code": region_of(inn),
                "now": now,
            },
        )

    cur = conn.execute(
        """
        INSERT INTO signals (inn, lead_key, type, signal_date, url, summary,
                              source, raw_path, dedup_key, created_at)
        VALUES (:inn, :lead_key, :type, :signal_date, :url, :summary,
                :source, :raw_path, :dedup_key, :now)
        ON CONFLICT(dedup_key) DO NOTHING
        """,
        {
            "inn": inn,
            "lead_key": None if inn else lead.dedup_key,
            "type": lead.signal_type,
            "signal_date": lead.signal_date,
            "url": lead.url,
            "summary": lead.summary,
            "source": lead.source,
            "raw_path": lead.raw_path,
            "dedup_key": lead.dedup_key,
            "now": now,
        },
    )
    signal_new = cur.rowcount > 0
    conn.commit()
    return IngestResult(company_new, company_updated, signal_new)


_ENRICHMENT_FIELDS = {"phone", "phone_extra", "site", "city", "email"}


def apply_enrichment(conn: sqlite3.Connection, inn: str, fields: dict) -> bool:
    """Точечно обновляет поля обогащения одной уже существующей компании,
    никогда не перетирая уже заполненное значение (4.3.2 ТЗ) — используется
    вне потока RawLead/ingest, например энричерами вроде enrich/site.py.
    fields — только ключи из _ENRICHMENT_FIELDS; остальное — ошибка
    вызывающего кода, а не тихий пропуск."""
    unknown = set(fields) - _ENRICHMENT_FIELDS
    if unknown:
        raise ValueError(f"apply_enrichment: нельзя обогащать поля {unknown}")
    if not fields:
        return False
    set_clause = ", ".join(f"{col} = COALESCE({col}, :{col})" for col in fields)
    cur = conn.execute(
        f"UPDATE companies SET {set_clause}, updated_at = :now WHERE inn = :inn",
        {**fields, "now": _now(), "inn": inn},
    )
    conn.commit()
    return cur.rowcount > 0
