from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from database import PostgresStandardStore  # noqa: E402
from schemas.product import CanonicalProduct  # noqa: E402
from schemas.product_schema import ProductExtraction  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync only verification-safe Standard JSON documents to PostgreSQL."
    )
    parser.add_argument("--extracted-dir", type=Path, default=ROOT / "data/cache/extracted")
    parser.add_argument("--schema-dir", type=Path, default=ROOT / "data/standard_json")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    if args.database_url:
        database_url = args.database_url
    else:
        from full_pipeline_batch import start_database

        database_url = start_database(use_embedded=True, database_url=None)
    store = PostgresStandardStore(database_url)
    store.ensure_schema()
    results = []
    for canonical_path in sorted(args.extracted_dir.glob("*.json")):
        document_id = canonical_path.stem
        schema_path = args.schema_dir / f"{document_id}.schema_v0.1.json"
        row = {"document_id": document_id, "db_saved": False, "reason": None}
        try:
            canonical = CanonicalProduct.model_validate_json(
                canonical_path.read_text(encoding="utf-8")
            )
            verification = canonical.extraction.verification
            contradicted = any(
                item.status == "FAIL" or item.verdict == "CONTRADICTED"
                for item in verification.items
            )
            if verification.status == "FAIL" or contradicted:
                row["reason"] = "verification FAIL/CONTRADICTED"
            elif not schema_path.exists():
                row["reason"] = "Standard JSON missing"
            else:
                standard = ProductExtraction.model_validate_json(
                    schema_path.read_text(encoding="utf-8")
                )
                blocked_fields = {
                    field: status.value
                    for field, status in standard.field_status.items()
                    if field in {"classes", "fees", "sales_charges"}
                    and status.value in {"AMBIGUOUS", "CONFLICT", "PARSE_FAILED"}
                }
                if blocked_fields:
                    row["reason"] = f"critical fields unresolved: {blocked_fields}"
                else:
                    store.save(document_id, standard)
                    row["db_saved"] = True
        except Exception as exc:
            row["reason"] = str(exc)
        results.append(row)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(row["db_saved"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
