from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DomainCapability:
    name: str
    knowledge_mode: str
    tools: tuple[str, ...]
    description: str


class DomainRegistry:
    """Extensibility seam for adding new financial domains without rewriting the graph."""

    def __init__(self, capabilities: Iterable[DomainCapability] | None = None) -> None:
        defaults = (
            DomainCapability("document", "unstructured", ("retriever",), "제공 문서/FAQ/설명문 근거"),
            DomainCapability("product", "structured", ("product_db",), "상품 속성·비교·조건 검색"),
            DomainCapability("law", "rule_temporal", ("legal_db",), "제도·세제·법령 및 시행시점"),
            DomainCapability("calculation", "deterministic", ("calculator",), "검증된 정책 기반 수치 계산"),
        )
        self._items = {item.name: item for item in (capabilities or defaults)}

    def get(self, name: str) -> DomainCapability | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return sorted(self._items)

    def describe(self) -> dict[str, dict[str, object]]:
        return {
            key: {"knowledge_mode": item.knowledge_mode, "tools": list(item.tools), "description": item.description}
            for key, item in sorted(self._items.items())
        }


# Source-aware evidence policy. Product ranking can complete from PostgreSQL
# rows; legal/tax still require Legal DB / Rule Engine; enterprise docs are
# primary for education and preferred (not forced) for product narrative.
EVIDENCE_POLICY = {
    "PRODUCT_FACT": {"required": ("product",), "optional": ()},
    "PRODUCT_RECOMMENDATION": {"required": ("product",), "optional": ("document",)},
    "PRODUCT_COMPARISON": {"required": ("product",), "optional": ("document",)},
    "GENERAL_EDUCATION": {"required": ("document",), "optional": ()},
    "TAX_CALCULATION": {"required": ("calculation",), "optional": ("law", "document")},
    "LEGAL_PROCEDURE": {"required": ("law", "document"), "optional": ()},
    "HYPOTHETICAL_EXAMPLE": {"required": (), "optional": ()},
}
