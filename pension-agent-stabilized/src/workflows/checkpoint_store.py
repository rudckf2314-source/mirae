from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class NodeCheckpointStore:
    """Versioned, atomic filesystem checkpoints for deterministic graph nodes."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load_model(
        self,
        document_hash: str,
        node: str,
        version: str,
        model: type[ModelT],
        *,
        document_id: str | None = None,
    ) -> ModelT | None:
        path = self._path(document_hash, node, version)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if envelope.get("document_hash") != document_hash:
                return None
            if document_id and envelope.get("document_id") != document_id:
                return None
            return model.model_validate(envelope["payload"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save_model(
        self,
        document_hash: str,
        node: str,
        version: str,
        value: BaseModel,
        *,
        document_id: str | None = None,
    ) -> None:
        path = self._path(document_hash, node, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "document_hash": document_hash,
            "document_id": document_id,
            "node": node,
            "node_version": version,
            "payload": value.model_dump(mode="json"),
        }
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def _path(self, document_hash: str, node: str, version: str) -> Path:
        safe_node = "".join(ch for ch in node if ch.isalnum() or ch in "_-")
        safe_version = "".join(ch for ch in version if ch.isalnum() or ch in "_.-")
        return self.root / document_hash[:2] / document_hash / f"{safe_node}.{safe_version}.json"
