from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .legal_store import DEFAULT_GUARDRAIL_PATH


class LegalRetrievalGuardrail:
    """Deny-by-default source scope evaluator for law retrieval."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or DEFAULT_GUARDRAIL_PATH)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def route_scope(self, topic: str) -> dict[str, Any]:
        route = self.data.get("routes", {}).get(topic)
        if not route:
            return {"allowed": False, "topic": topic, "source_keys": [], "allowed_articles": {}}
        registry = self.data.get("source_registry", {})
        bundles = self.data.get("bundles", {})
        keys: list[str] = []
        for bundle in route.get("bundles", []):
            for key in bundles.get(bundle, []):
                if key not in keys:
                    keys.append(key)
        allowed_articles: dict[str, set[str]] = {}
        for key in keys:
            spec = registry.get(key)
            if not spec:
                continue
            if spec.get("default_scope") == "PARTIAL":
                allowed_articles[key] = set(spec.get("allowed_articles", []))
        return {"allowed": bool(keys), "topic": topic, "source_keys": keys, "allowed_articles": allowed_articles}

    def source_allowed(self, source_key: str, article_no: str | None = None) -> bool:
        spec = self.data.get("source_registry", {}).get(source_key)
        if not spec:
            return False
        if spec.get("default_scope") == "FULL":
            return True
        return article_no in set(spec.get("allowed_articles", []))
