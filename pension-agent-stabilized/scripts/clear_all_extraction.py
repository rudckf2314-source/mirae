from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CACHE_DIRS = [
    ROOT / "data/cache/extracted",
    ROOT / "data/cache/parsed",
    ROOT / "data/standard_json",
    ROOT / "data/cache/node_checkpoints",
]
INDEX = ROOT / "data/cache/index.json"
PG_URL = ROOT / "data/postgres/database_url.txt"

TRUNCATE_SQL = """
TRUNCATE TABLE
  extraction_issues, field_status, evidence, narratives,
  financial_metrics, capital_flows, performance, hedging_policies,
  master_feeder_relations, fund_conversion_rules, class_transition_rules,
  fees, sales_charges, product_classes, risk_ratings, products,
  source_documents
RESTART IDENTITY CASCADE
"""


def clear_json_dirs() -> dict[str, int]:
    counts: dict[str, int] = {}
    for directory in CACHE_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
        if directory.name == "node_checkpoints":
            removed = sum(1 for _ in directory.rglob("*") if _.is_file())
            shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)
        else:
            removed = sum(1 for path in directory.glob("*.json") if not path.unlink())
        counts[directory.name] = removed
    INDEX.write_text('{"documents": []}', encoding="utf-8")
    return counts


def truncate_db() -> str:
    if not PG_URL.exists():
        return "skipped: no database_url.txt"
    import psycopg

    url = PG_URL.read_text(encoding="utf-8").strip()
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(TRUNCATE_SQL)
            conn.commit()
        return "truncated"
    except Exception as exc:
        return f"error: {exc}"


def main() -> int:
    counts = clear_json_dirs()
    db_status = truncate_db()
    print(f"cleared={counts}")
    print(f"index_reset=True")
    print(f"db={db_status}")
    print(
        "remaining",
        len(list((ROOT / "data/cache/extracted").glob("*.json"))),
        len(list((ROOT / "data/standard_json").glob("*.json"))),
        len(list((ROOT / "data/cache/node_checkpoints").rglob("*.json"))),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
