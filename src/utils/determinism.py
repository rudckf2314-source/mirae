from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from schemas.product import CanonicalProduct

FINGERPRINT_VERSION = "v3"
FINGERPRINT_PREFIX = f"DETERMINISM_FINGERPRINT:{FINGERPRINT_VERSION}:"


def canonical_fact_fingerprint(product: CanonicalProduct) -> str:
    """Hash canonical facts while excluding run/document-specific provenance."""
    payload = {
        "document": {
            "hash": product.document.document_hash,
            "as_of_date": product.document.as_of_date,
            "effective_date": product.document.effective_date,
        },
        "product": {
            "name": product.product.name,
            "manager": product.product.manager,
            "asset_type": product.product.asset_type,
            "fund_code": product.product.fund_code,
            "classification": product.product.classification,
            "risk": {
                "grade": product.product.risk.grade,
                "label": product.product.risk.label,
            },
            "investment_objective": product.product.investment_objective.text,
            "investment_strategy": product.product.investment_strategy.text,
            "investment_risks": _facts(product.product.investment_risks),
        },
        "classes": _facts(product.classes),
        "fees": _facts(product.fees),
        "performance": _facts(product.performance),
        "aum": _facts(product.aum),
    }
    encoded = json.dumps(_normalize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def attach_fingerprint(product: CanonicalProduct, fingerprint: str) -> None:
    product.extraction.warnings = [
        item for item in product.extraction.warnings
        if not item.startswith("DETERMINISM_FINGERPRINT:")
    ]
    product.extraction.audit = [
        item for item in product.extraction.audit
        if not item.startswith("DETERMINISM_FINGERPRINT:")
    ]
    product.extraction.audit.append(f"{FINGERPRINT_PREFIX}{fingerprint}")


def stored_fingerprint(product: CanonicalProduct | None) -> str | None:
    if product is None:
        return None
    for warning in [*product.extraction.audit, *product.extraction.warnings]:
        if warning.startswith(FINGERPRINT_PREFIX):
            return warning[len(FINGERPRINT_PREFIX):].strip() or None
    return None

def _facts(items: list[Any]) -> list[dict[str, Any]]:
    values = []
    for item in items:
        raw = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        raw.pop("evidence_refs", None)
        values.append(_normalize(raw))
    return sorted(values, key=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True))


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", "", value).strip()
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value
