"""Deterministic Evidence Hub used by the LangGraph execution path only."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EvidenceDomain = Literal["product", "document", "law", "calculation"]
EvidenceStatus = Literal["matched", "missing", "unresolved", "conflict"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    domain: EvidenceDomain
    claim_key: str
    claim_value: Any = None
    source_type: str | None = None
    source_file: str | None = None
    source_page: int | str | None = None
    source_locator: str | None = None
    source_version: str | None = None
    retrieval_method: str
    origin_text: str | None = None
    status: EvidenceStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceHub:
    """Normalizes raw worker output without changing or discarding it."""

    @staticmethod
    def _id(domain: str, source: str | None, locator: str | None) -> str:
        value = f"{domain}|{source or ''}|{locator or ''}"
        return f"ev_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _version(versions: dict[str, str | None], domain: str) -> str | None:
        keys = {
            "product": ("product_db", "combined"),
            "document": ("pdf_index", "combined"),
            "law": ("law_policy", "combined"),
        }.get(domain, ("combined",))
        for key in keys:
            if versions.get(key):
                return versions[key]
        return None

    def collect(
        self, raw_result: dict[str, Any], source_versions: dict[str, str | None]
    ) -> tuple[list[Evidence], dict[str, dict[str, int]]]:
        items: list[Evidence] = []
        product_version = self._version(source_versions, "product")
        document_version = self._version(source_versions, "document")
        law_version = self._version(source_versions, "law")
        calculation = raw_result.get("calculation_result")
        if calculation:
            locator = f"{calculation.get('formula_id')}:{calculation.get('formula_version')}"
            items.append(Evidence(evidence_id=self._id("calculation", "calculation_rule", locator), domain="calculation", claim_key=str(calculation.get("calculation_type")), claim_value=calculation.get("result"), source_type="calculation_rule", source_file=None, source_page=None, source_locator=locator, source_version=str(calculation.get("formula_version")), retrieval_method="calculation_worker", origin_text=None, status="matched", metadata={"inputs": calculation.get("inputs"), "intermediate_values": calculation.get("intermediate_values"), "unit": calculation.get("unit"), "rounding": calculation.get("rounding_applied"), "decimal": True}))

        for product in raw_result.get("product_results") or []:
            source_file = product.get("source_file")
            source_pages = product.get("source_pages") or []
            for raw in product.get("evidence") or []:
                page = raw.get("page")
                locator = str(raw.get("evidence_id") or f"{raw.get('field_path')}:{page}")
                status = self._product_evidence_status(raw_result, product, raw)
                items.append(Evidence(
                    evidence_id=self._id("product", source_file, locator), domain="product",
                    claim_key=str(raw.get("field_path") or "product"),
                    claim_value=raw.get("value"), source_type="structured_product_json",
                    source_file=source_file, source_page=page, source_locator=locator,
                    source_version=product_version, retrieval_method="product_db",
                    origin_text=raw.get("source_text"), status=status,
                    metadata={"product_name": product.get("product_name"), "class_name": product.get("class_name"), "raw_evidence_id": raw.get("evidence_id")},
                ))
            if not product.get("evidence"):
                locator = str(product.get("record_id") or product.get("product_name") or "product")
                items.append(Evidence(
                    evidence_id=self._id("product", source_file, locator), domain="product",
                    claim_key="product", claim_value=product.get("product_name"), source_type="structured_product_json",
                    source_file=source_file, source_page=(source_pages[0] if source_pages else None), source_locator=locator,
                    source_version=product_version, retrieval_method="product_db", origin_text=None, status="missing",
                    metadata={"product_name": product.get("product_name"), "class_name": product.get("class_name")},
                ))

        for pdf in raw_result.get("pdf_evidence") or []:
            source_file, page = pdf.get("source_file"), pdf.get("source_page")
            chunks = pdf.get("chunks") or []
            status = pdf.get("status") or ("matched" if chunks else "unresolved")
            for chunk in chunks or [{}]:
                locator = str(chunk.get("chunk_id") or f"page:{page}")
                items.append(Evidence(
                    evidence_id=self._id("document", source_file, locator), domain="document",
                    claim_key="pdf_chunk", claim_value=pdf.get("fields") or [], source_type="pdf_chunk",
                    source_file=source_file, source_page=page, source_locator=locator,
                    source_version=document_version, retrieval_method="direct_pdf_lookup",
                    origin_text=chunk.get("text"), status=status,
                    metadata={"product_name": pdf.get("product_name"), "class_name": pdf.get("class_name"), "fields": pdf.get("fields") or []},
                ))

        for context in raw_result.get("results") or []:
            source_file, page = context.get("filename"), context.get("location")
            locator = str(context.get("chunk_id") or context.get("document_id") or f"{context.get('location_type')}:{page}")
            items.append(Evidence(
                evidence_id=self._id("document", source_file, locator), domain="document",
                claim_key="document_chunk", claim_value=context.get("text"), source_type="document_chunk",
                source_file=source_file, source_page=page, source_locator=locator,
                source_version=document_version, retrieval_method="retriever",
                origin_text=context.get("text"), status="matched",
                metadata={"location_type": context.get("location_type"), "document_id": context.get("document_id")},
            ))

        law_result = raw_result.get("law_result") or {}
        law_status: EvidenceStatus = "matched" if law_result.get("success") else "missing"
        for kind, sources in (("primary", law_result.get("primary_sources") or []), ("reference", law_result.get("references") or [])):
            for source in sources:
                law_name, article = source.get("law_name"), source.get("article_no")
                locator = f"{law_name or ''}:{article or ''}:{kind}"
                paragraphs = source.get("paragraphs") or []
                # Reference origin text is a distinct limiting context.  Do not
                # fabricate it from a paragraph when the API did not provide it.
                text = source.get("origin_text")
                if kind == "primary" and not text:
                    text = "\n".join(str(p.get("text") or "") for p in paragraphs)
                items.append(Evidence(
                    evidence_id=self._id("law", law_name, locator), domain="law",
                    claim_key="law_primary" if kind == "primary" else "law_reference",
                    claim_value={"law_name": law_name, "article_no": article, "article_title": source.get("article_title")},
                    source_type=str(source.get("source_type") or "law_api"), source_file=law_name, source_page=None, source_locator=locator,
                    source_version=law_version or source.get("effective_date") or source.get("article_effective_date"),
                    retrieval_method="law_tool", origin_text=text or None, status=law_status,
                    metadata={"kind": kind, "effective_date": source.get("effective_date"), "article_effective_date": source.get("article_effective_date"), "paragraphs": paragraphs, "source_key": source.get("source_key"), "source_channel": source.get("source_channel"), "fetched_at": source.get("fetched_at")},
                ))

        deduped = {item.evidence_id: item for item in items}
        evidence = list(deduped.values())
        summary = {
            "domain": dict(Counter(item.domain for item in evidence)),
            "status": dict(Counter(item.status for item in evidence)),
        }
        return evidence, summary

    @staticmethod
    def _product_evidence_status(
        raw_result: dict[str, Any], product: dict[str, Any], raw_evidence: dict[str, Any]
    ) -> EvidenceStatus:
        """Carry forward the existing Product→PDF validation status unchanged."""
        status_item = next(
            (
                item for item in raw_result.get("evidence_status") or []
                if item.get("product_name") == product.get("product_name")
                and item.get("class_name") in (None, product.get("class_name"))
            ),
            None,
        )
        if raw_evidence.get("field_path") and raw_evidence.get("value") is not None:
            return "matched"
        if not status_item:
            return "matched"
        path = str(raw_evidence.get("field_path") or "")
        field_groups = {
            "product_identity": path.startswith("product"),
            "class": "classes" in path,
            "pension_type": "classes" in path,
            "online": "classes" in path,
            "risk_grade": path.startswith("risk_ratings"),
            "total_fee": path.startswith("fees"),
        }
        statuses = [
            field.get("status") for field in status_item.get("fields") or []
            if field_groups.get(str(field.get("field")), False)
        ]
        if not statuses:
            return "matched"
        for candidate in ("conflict", "unresolved", "missing", "matched"):
            if candidate in statuses:
                return candidate  # type: ignore[return-value]
        return "matched"


def evidence_json(evidence: list[Evidence]) -> list[dict[str, Any]]:
    """Small explicit serializer used in state; raw worker results stay separate."""
    return [item.model_dump(mode="json") for item in evidence]
