from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pgserver  # noqa: E402
from database.postgres_store import PostgresStandardStore  # noqa: E402
from schemas.product_schema import ProductExtraction  # noqa: E402


def dedupe_classes(product: ProductExtraction) -> ProductExtraction:
    seen: set[str] = set()
    classes = []
    for item in product.classes:
        if item.class_key in seen:
            continue
        seen.add(item.class_key)
        classes.append(item)
    if len(classes) == len(product.classes):
        return product
    payload = product.model_dump()
    payload["classes"] = [item.model_dump() for item in classes]
    return ProductExtraction.model_validate(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Force backfill Standard JSON into PostgreSQL.")
    parser.add_argument("--document-id", action="append", default=[])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    pg_dir = ROOT / "data" / "postgres"
    server = pgserver.get_server(pg_dir)
    database_url = server.get_uri()
    (pg_dir / "database_url.txt").write_text(database_url, encoding="utf-8")

    store = PostgresStandardStore(database_url)
    store.ensure_schema()
    standard_dir = ROOT / "data" / "standard_json"

    import psycopg

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT document_id FROM source_documents")
            in_db = {row[0] for row in cur.fetchall()}

    targets = args.document_id or sorted(
        path.name.replace(".schema_v0.1.json", "")
        for path in standard_dir.glob("*.schema_v0.1.json")
        if path.name.replace(".schema_v0.1.json", "") not in in_db
    )
    if not targets:
        logging.info("backfill 대상 없음")
        return 0

    saved = failed = 0
    for document_id in targets:
        path = standard_dir / f"{document_id}.schema_v0.1.json"
        if not path.exists():
            logging.error("standard json 없음: %s", document_id)
            failed += 1
            continue
        try:
            product = ProductExtraction.model_validate_json(path.read_text(encoding="utf-8"))
            product = dedupe_classes(product)
            store.save(document_id, product, unsafe=True)
            saved += 1
            logging.info("force saved: %s", document_id)
        except Exception:
            logging.exception("force backfill 실패: %s", document_id)
            failed += 1

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM source_documents")
            total = cur.fetchone()[0]

    logging.info("force backfill complete saved=%s failed=%s db_total=%s", saved, failed, total)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
