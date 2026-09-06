from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from schemas.product_schema import ProductExtraction

out = ROOT / "schemas" / "product_schema_v0.1.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(ProductExtraction.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
print(out)
