from __future__ import annotations

import json
from pathlib import Path

from schemas.product_schema import ProductExtraction


class StandardJsonStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, document_id: str, product: ProductExtraction) -> Path:
        path = self.root / f"{document_id}.schema_v0.1.json"
        path.write_text(
            json.dumps(product.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
