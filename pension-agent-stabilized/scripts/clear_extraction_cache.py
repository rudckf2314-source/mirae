from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CACHE_DIRS = [
    ROOT / "data" / "cache" / "extracted",
    ROOT / "data" / "cache" / "parsed",
]
STANDARD_DIR = ROOT / "data" / "standard_json"
INDEX_PATH = ROOT / "data" / "cache" / "index.json"
PG_URL_FILE = ROOT / "data" / "postgres" / "database_url.txt"

TRUNCATE_SQL = """
TRUNCATE TABLE
  extraction_issues, field_status, evidence, narratives,
  financial_metrics, capital_flows, performance, hedging_policies,
  master_feeder_relations, fund_conversion_rules, class_transition_rules,
  fees, sales_charges, product_classes, risk_ratings, products,
  source_documents
RESTART IDENTITY CASCADE
"""


def main() -> int:
    counts: dict[str, int] = {}
    for directory in CACHE_DIRS:
        removed = 0
        if directory.exists():
            for path in directory.glob("*.json"):
                path.unlink()
                removed += 1
        counts[directory.name] = removed

    standard_removed = 0
    if STANDARD_DIR.exists():
        for path in STANDARD_DIR.glob("*.json"):
            path.unlink()
            standard_removed += 1
    counts["standard_json"] = standard_removed

    INDEX_PATH.write_text('{"documents": []}', encoding="utf-8")

    db_status = "skipped"
    if PG_URL_FILE.exists():
        import psycopg

        url = PG_URL_FILE.read_text(encoding="utf-8").strip()
        try:
            with psycopg.connect(url) as conn:
                with conn.cursor() as cur:
                    cur.execute(TRUNCATE_SQL)
                conn.commit()
            db_status = "truncated"
        except Exception as exc:
            db_status = f"error: {exc}"

    remaining_extracted = len(list((ROOT / "data/cache/extracted").glob("*.json")))
    remaining_standard = len(list(STANDARD_DIR.glob("*.json")))

    print(f"cleared={counts}")
    print(f"index_reset=True")
    print(f"db={db_status}")
    print(f"remaining extracted={remaining_extracted} standard={remaining_standard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
