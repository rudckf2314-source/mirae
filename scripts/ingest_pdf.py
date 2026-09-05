from __future__ import annotations

import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.extraction_service import ExtractionService

parser = argparse.ArgumentParser(description="PDF -> Canonical JSON -> Schema JSON -> PostgreSQL")
parser.add_argument("pdf", type=Path)
parser.add_argument("--force", action="store_true")
args = parser.parse_args()

service = ExtractionService()
result = service.process_pdf(args.pdf, force=args.force)
if result.error:
    raise SystemExit(result.error)
print(f"document_id={result.product.document.document_id if result.product else '-'}")
print(f"schema_json={result.standard_json_path}")
print(f"db_saved={result.db_saved}")
if result.db_error:
    print(f"db_error={result.db_error}")
