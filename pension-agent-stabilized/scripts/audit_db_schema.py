"""Static + optional live PostgreSQL schema audit."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"


def parse_sql_schema(sql_dir: Path) -> dict:
    tables: dict[str, dict] = {}
    for path in sorted(sql_dir.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\);",
            text,
            flags=re.S | re.I,
        ):
            name = match.group(1)
            body = match.group(2)
            pk = []
            fks = []
            uniques = []
            columns = []
            for line in body.splitlines():
                raw = line.strip().rstrip(",")
                if not raw or raw.startswith("--"):
                    continue
                columns.append(raw)
                if "PRIMARY KEY" in raw.upper() and not raw.upper().startswith("UNIQUE"):
                    if raw.upper().startswith("PRIMARY KEY"):
                        pk.extend(re.findall(r"\((\w+)\)", raw))
                    else:
                        col = raw.split()[0]
                        if "PRIMARY KEY" in raw.upper():
                            pk.append(col)
                if "REFERENCES" in raw.upper():
                    ref = re.search(
                        r"REFERENCES\s+(\w+)\((\w+)\)", raw, flags=re.I
                    )
                    col = raw.split()[0]
                    if ref:
                        fks.append(
                            {
                                "column": col,
                                "references": f"{ref.group(1)}({ref.group(2)})",
                            }
                        )
                if raw.upper().startswith("UNIQUE"):
                    cols = re.findall(r"\(([^)]+)\)", raw)
                    if cols:
                        uniques.append([c.strip() for c in cols[0].split(",")])
                elif " UNIQUE" in raw.upper() and not raw.upper().startswith("CREATE"):
                    # column-level unique e.g. document_id TEXT NOT NULL UNIQUE
                    uniques.append([raw.split()[0]])
            tables[name] = {
                "source_file": path.name,
                "primary_key": pk or (["id"] if any(c.startswith("id ") for c in columns) else []),
                "foreign_keys": fks,
                "unique_constraints": uniques,
                "column_defs": columns,
            }
        # Alter-added uniques / drops are informational.
        if "DROP CONSTRAINT IF EXISTS source_documents_file_hash_key" in text:
            if "source_documents" in tables:
                tables["source_documents"]["notes"] = [
                    "file_hash UNIQUE dropped in 002_allow_duplicate_file_hash.sql"
                ]
    return {
        "sql_dir": str(sql_dir),
        "migration_files": [p.name for p in sorted(sql_dir.glob("*.sql"))],
        "table_count": len(tables),
        "tables": tables,
    }


def live_schema_audit(database_url: str) -> dict | None:
    try:
        import psycopg
    except ImportError:
        return {"error": "psycopg not installed"}
    try:
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version(), current_database()")
                version, db = cur.fetchone()
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public' AND table_type='BASE TABLE'
                    ORDER BY table_name
                    """
                )
                table_names = [row[0] for row in cur.fetchall()]
                cur.execute(
                    """
                    SELECT tc.table_name, kcu.column_name, tc.constraint_type
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.table_schema='public'
                      AND tc.constraint_type IN ('PRIMARY KEY','UNIQUE','FOREIGN KEY')
                    ORDER BY tc.table_name, tc.constraint_type, kcu.ordinal_position
                    """
                )
                constraints = [
                    {"table": r[0], "column": r[1], "type": r[2]} for r in cur.fetchall()
                ]
        return {
            "connected": True,
            "version": version,
            "database": db,
            "tables": table_names,
            "table_count": len(table_names),
            "constraints": constraints,
        }
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data/cache/db_schema_audit.json",
    )
    args = parser.parse_args()
    payload = {
        "static_sql": parse_sql_schema(SQL_DIR),
        "live": None,
        "database_url_configured": bool(os.environ.get("DATABASE_URL")),
        "docker_compose": str(ROOT / "src/docker-compose.yml"),
        "expected_loader_tables": [
            "source_documents",
            "products",
            "risk_ratings",
            "product_classes",
            "sales_charges",
            "fees",
            "class_transition_rules",
            "fund_conversion_rules",
            "master_feeder_relations",
            "hedging_policies",
            "performance",
            "capital_flows",
            "financial_metrics",
            "investment_profiles",
            "liquidity_rules",
            "narratives",
            "evidence",
            "field_status",
            "extraction_issues",
        ],
    }
    url = os.environ.get("DATABASE_URL")
    if url:
        payload["live"] = live_schema_audit(url)
    else:
        payload["live"] = {
            "connected": False,
            "error": "DATABASE_URL not set; live schema probe skipped",
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(args.out),
        "static_table_count": payload["static_sql"]["table_count"],
        "migration_files": payload["static_sql"]["migration_files"],
        "live": payload["live"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
