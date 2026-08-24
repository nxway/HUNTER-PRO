"""
enrich/fns_bulk.py — массовые наборы открытых данных ФНС: выручка, налоговый
режим, численность (этап 4 ТЗ). Контракт 5.2: load_dataset(path), enrich_all().

Обогащение из ФНС — пакетное, а не поштучное (5.2 ТЗ): файл скачивается
целиком заранее (вручную, раз в год — см. раздел 4 ТЗ), сюда передаётся уже
лежащий на диске путь. Сетевых запросов этот модуль не делает вообще —
поэтому, в отличие от sources/fsa_declarations.py и enrich/site.py/dgis.py,
его вообще нечем было "не проверить из-за сети". Не проверено другое:
реальный формат самого файла ФНС (см. ЗОНА РИСКА ниже) — ФНС публикует
несколько разных открытых наборов (выручка из ГИР БО, спецрежимы,
среднесписочная численность), в разных версиях это CSV или XML с разными
названиями колонок, и без образца настоящего файла угадать их точно нельзя.

ЧТО ПРИСЛАТЬ, ЧТОБЫ ДОТОЧИТЬ: скачайте один из открытых наборов ФНС
(nalog.gov.ru/opendata/) — подойдёт любой из трёх (выручка/спецрежим/
численность) — и пришлите первые 5-10 строк (шапку + пример) как есть,
в оригинальной кодировке/разделителе. Поддержан пока только CSV/TSV с
настраиваемым сопоставлением колонок (_COLUMN_ALIASES ниже) — если реальный
файл окажется XML (это тоже частый формат у ФНС), понадобится отдельный
разбор, который без образца писать вслепую бессмысленно.

Запуск как отдельный модуль:
    python -m enrich.fns_bulk --load path/to/dataset.csv --dataset revenue
    python -m enrich.fns_bulk --enrich-all
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from typing import Optional

import db
from enrich.inn import normalize_name, validate as validate_inn

# ЗОНА РИСКА: реальные названия колонок в открытых данных ФНС не проверены.
# Ключ — каноничное поле registry, значение — варианты заголовков колонки,
# которые встречаются на практике (нижний регистр, без пробуждения регистра
# сравнения ниже). Допишите сюда заголовок из реального файла, если он не
# совпал ни с одним вариантом.
_COLUMN_ALIASES: dict[str, list[str]] = {
    "inn": ["инн", "inn"],
    "name": ["наименование", "название", "полное наименование", "name"],
    "city": ["город", "населенный пункт", "населённый пункт"],
    "region": ["регион", "код региона", "субъект рф"],
    "okved": ["оквэд", "оквэд2", "код оквэд"],
    "revenue": ["выручка", "доходы", "revenue"],
    "tax_regime": ["режим налогообложения", "спецрежим", "налоговый режим"],
    "employees": ["среднесписочная численность", "численность", "employees"],
}

_TAX_REGIME_MAP = {
    "осно": "osno",
    "усн": "usn",
    "енвд": "usn",
    "псн": "usn",
    "есхн": "usn",
}


def _detect_delimiter(sample: str) -> str:
    return ";" if sample.count(";") > sample.count(",") else ","


def _map_headers(header_row: list[str]) -> dict[str, int]:
    """Сопоставляет реальные заголовки колонок каноничным полям по
    _COLUMN_ALIASES. Колонка, которую не удалось узнать, просто
    пропускается — это не смертельно, лишь бы был ИНН и хоть что-то ещё."""
    lowered = [h.strip().lower() for h in header_row]
    mapping: dict[str, int] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        for i, h in enumerate(lowered):
            if h in aliases:
                mapping[field] = i
                break
    return mapping


def _parse_revenue(raw: str) -> Optional[int]:
    if not raw:
        return None
    digits = raw.replace(" ", "").replace(",", ".").split(".")[0]
    digits = "".join(c for c in digits if c.isdigit() or c == "-")
    return int(digits) if digits and digits not in ("-",) else None


def _normalize_tax_regime(raw: str) -> Optional[str]:
    if not raw:
        return None
    return _TAX_REGIME_MAP.get(raw.strip().lower(), "unknown")


def load_dataset(path: str, dataset: str = "revenue") -> int:
    """Разбирает скачанный файл ФНС (CSV/TSV) и раскладывает по registry —
    справочнику страны по ИНН (15.3 ТЗ), UPSERT-ом. `dataset` пока не
    меняет разбор (все поля читаются из одной строки, если они там есть) —
    параметр оставлен для дальнейшей специализации под разные наборы,
    когда появятся образцы реальных файлов.

    Возвращает число загруженных строк."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = _detect_delimiter(sample)
        reader = csv.reader(f, delimiter=delimiter)

        try:
            header = next(reader)
        except StopIteration:
            return 0
        mapping = _map_headers(header)
        if "inn" not in mapping:
            raise ValueError(
                f"load_dataset: в файле {path} не нашёл колонку ИНН среди заголовков {header} — "
                f"допишите вариант заголовка в _COLUMN_ALIASES['inn']"
            )

        conn = db.init_db()
        loaded = 0
        for row in reader:
            if not row or len(row) <= mapping["inn"]:
                continue
            inn = row[mapping["inn"]].strip()
            if not validate_inn(inn):
                continue

            name = row[mapping["name"]].strip() if "name" in mapping and mapping["name"] < len(row) else ""
            city = row[mapping["city"]].strip() if "city" in mapping and mapping["city"] < len(row) else None
            region = row[mapping["region"]].strip() if "region" in mapping and mapping["region"] < len(row) else None
            okved = row[mapping["okved"]].strip() if "okved" in mapping and mapping["okved"] < len(row) else None
            revenue = (
                _parse_revenue(row[mapping["revenue"]])
                if "revenue" in mapping and mapping["revenue"] < len(row)
                else None
            )
            tax_regime = (
                _normalize_tax_regime(row[mapping["tax_regime"]])
                if "tax_regime" in mapping and mapping["tax_regime"] < len(row)
                else None
            )
            employees = (
                _parse_revenue(row[mapping["employees"]])
                if "employees" in mapping and mapping["employees"] < len(row)
                else None
            )

            conn.execute(
                """
                INSERT INTO registry (inn, name_raw, name_norm, city, region, okved, revenue, tax_regime, employees)
                VALUES (:inn, :name_raw, :name_norm, :city, :region, :okved, :revenue, :tax_regime, :employees)
                ON CONFLICT(inn) DO UPDATE SET
                    name_raw   = COALESCE(excluded.name_raw, registry.name_raw),
                    name_norm  = COALESCE(excluded.name_norm, registry.name_norm),
                    city       = COALESCE(excluded.city, registry.city),
                    region     = COALESCE(excluded.region, registry.region),
                    okved      = COALESCE(excluded.okved, registry.okved),
                    revenue    = COALESCE(excluded.revenue, registry.revenue),
                    tax_regime = COALESCE(excluded.tax_regime, registry.tax_regime),
                    employees  = COALESCE(excluded.employees, registry.employees)
                """,
                {
                    "inn": inn,
                    "name_raw": name or None,
                    "name_norm": normalize_name(name) if name else None,
                    "city": city or None,
                    "region": int(region) if region and region.isdigit() else None,
                    "okved": okved or None,
                    "revenue": revenue,
                    "tax_regime": tax_regime,
                    "employees": employees,
                },
            )
            loaded += 1

        conn.commit()
        conn.close()

    return loaded


def _revenue_trend(current: Optional[int], prev: Optional[int]) -> Optional[str]:
    if current is None or prev is None or prev == 0:
        return None
    change = (current - prev) / prev
    if change > 0.05:
        return "рост"
    if change < -0.05:
        return "падение"
    return "ровно"


def enrich_all() -> int:
    """Проставляет выручку (со сдвигом в историю revenue_prev/revenue_prev2),
    налоговый режим и численность нашим companies по registry. Компанию,
    которой нет в свежем registry, помечает legal_status='dead' (15.2 ТЗ:
    "если ИНН нет в наборах последних лет — фирма не действует"). Ручные
    поля (my_status, note и т.п.) эта функция не трогает вообще — она
    пишет только в источниковые поля companies, как и apply_enrichment
    (4.3.2 ТЗ).

    Возвращает число обновлённых компаний."""
    conn = db.init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows = conn.execute("SELECT inn, revenue, revenue_prev FROM companies").fetchall()
    updated = 0
    for row in rows:
        inn = row["inn"]
        reg = conn.execute(
            "SELECT revenue, tax_regime, employees FROM registry WHERE inn = ?", (inn,)
        ).fetchone()

        if reg is None:
            conn.execute(
                "UPDATE companies SET legal_status = 'dead', updated_at = ? WHERE inn = ?",
                (now, inn),
            )
            continue

        new_revenue = reg["revenue"]
        fields = {"legal_status": "active"}
        if new_revenue is not None and new_revenue != row["revenue"]:
            fields["revenue_prev2"] = row["revenue_prev"]
            fields["revenue_prev"] = row["revenue"]
            fields["revenue"] = new_revenue
            fields["revenue_trend"] = _revenue_trend(new_revenue, row["revenue"])
        if reg["tax_regime"]:
            fields["vat_guess"] = reg["tax_regime"]
        if reg["employees"] is not None:
            fields["employees"] = reg["employees"]

        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        conn.execute(
            f"UPDATE companies SET {set_clause}, updated_at = :now WHERE inn = :inn",
            {**fields, "now": now, "inn": inn},
        )
        updated += 1

    conn.commit()
    conn.close()
    return updated


def _main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка наборов ФНС и обогащение companies")
    parser.add_argument("--load", metavar="PATH", help="путь к скачанному файлу ФНС")
    parser.add_argument("--dataset", default="revenue", choices=["revenue", "tax_regime", "headcount"])
    parser.add_argument("--enrich-all", action="store_true", help="проставить выручку/режим/численность по нашим companies")
    args = parser.parse_args()

    from rich.console import Console

    console = Console()
    if args.load:
        try:
            n = load_dataset(args.load, dataset=args.dataset)
        except (OSError, ValueError) as exc:
            console.print(f"[red]fns_bulk: не удалось загрузить {args.load} — {exc}[/red]")
            raise SystemExit(1)
        console.print(f"fns_bulk: загружено в registry {n} строк из {args.load}")
    if args.enrich_all:
        n = enrich_all()
        console.print(f"fns_bulk: обогащено {n} компаний")
    if not args.load and not args.enrich_all:
        parser.print_help()


if __name__ == "__main__":
    _main()
