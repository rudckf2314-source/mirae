from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from exceptions import CacheWriteError
from schemas.document import ParsedDocument
from schemas.product import CanonicalProduct

from .base import ProductRepository


class JsonCacheRepository(ProductRepository):
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.pdf_dir = self.cache_dir / "pdf"
        self.parsed_dir = self.cache_dir / "parsed"
        self.extracted_dir = self.cache_dir / "extracted"
        self.index_path = self.cache_dir / "index.json"
        self._ensure_dirs()

    def save_product(
        self,
        product: CanonicalProduct,
        pdf_bytes: bytes | None = None,
        parsed: ParsedDocument | None = None,
    ) -> CanonicalProduct:
        document_id = product.document.document_id
        try:
            if pdf_bytes is not None:
                (self.pdf_dir / f"{document_id}.pdf").write_bytes(pdf_bytes)
            if parsed is not None:
                self._write_json(self.parsed_dir / f"{document_id}.json", parsed.model_dump())
            extracted_path = self.extracted_dir / f"{document_id}.json"
            self._write_json(extracted_path, product.model_dump())
            self._upsert_index(product, extracted_path)
        except Exception as exc:
            raise CacheWriteError(f"캐시 저장 실패: {document_id}") from exc
        return product

    def get_by_document_id(self, document_id: str) -> CanonicalProduct | None:
        path = self.extracted_dir / f"{document_id}.json"
        if not path.exists():
            return None
        return CanonicalProduct.model_validate(self._read_json(path))

    def get_by_hash(self, document_hash: str) -> CanonicalProduct | None:
        for item in self._load_index().get("documents", []):
            if item.get("document_hash") == document_hash:
                document_id = item.get("document_id")
                if document_id:
                    return self.get_by_document_id(document_id)
        for path in self.extracted_dir.glob("*.json"):
            payload = self._read_json(path)
            if payload.get("document", {}).get("document_hash") == document_hash:
                return CanonicalProduct.model_validate(payload)
        return None

    def list_products(self) -> list[dict]:
        documents = self._load_index().get("documents", [])
        documents.sort(key=lambda item: item.get("processed_at") or "", reverse=True)
        return documents

    def get_parsed(self, document_id: str) -> ParsedDocument | None:
        path = self.parsed_dir / f"{document_id}.json"
        if not path.exists():
            return None
        return ParsedDocument.model_validate(self._read_json(path))

    def get_pdf_bytes(self, document_id: str) -> bytes | None:
        path = self.pdf_dir / f"{document_id}.pdf"
        if not path.exists():
            return None
        return path.read_bytes()

    def _ensure_dirs(self) -> None:
        for path in (self.cache_dir, self.pdf_dir, self.parsed_dir, self.extracted_dir):
            path.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_json(self.index_path, {"documents": []})

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"documents": []}
        return self._read_json(self.index_path)

    def _upsert_index(self, product: CanonicalProduct, extracted_path: Path) -> None:
        index = self._load_index()
        documents: list[dict[str, Any]] = index.setdefault("documents", [])
        record = {
            "document_id": product.document.document_id,
            "document_hash": product.document.document_hash,
            "file_name": product.document.file_name,
            "status": product.extraction.status,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "canonical_json_path": str(extracted_path.as_posix()),
            "product_name": product.product.name,
            "risk_grade": product.product.risk.grade,
            "risk_label": product.product.risk.label,
        }
        documents[:] = [item for item in documents if item.get("document_id") != record["document_id"]]
        documents.append(record)
        self._write_json(self.index_path, index)

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
