from __future__ import annotations

from schemas.chunk import Chunk
from schemas.product import CanonicalProduct, VerificationItem
from verification.text import compact, contains_any_date, contains_text, format_value


def verify_text(
    field_path: str,
    value: str | None,
    evidence_texts: list[str],
    evidence_refs: list[str],
    kind: str = "text",
) -> VerificationItem:
    base = {
        "field_path": field_path,
        "method": "deterministic",
        "extracted_value": format_value(value),
        "evidence_refs": list(evidence_refs),
    }
    if value is None or (isinstance(value, str) and not value.strip()):
        return VerificationItem(status="SKIPPED", verdict="NOT_APPLICABLE", reason="추출값이 비어 있습니다.", **base)
    if not evidence_refs:
        return VerificationItem(status="SKIPPED", verdict="UNVERIFIABLE", reason="evidence_ref가 없습니다.", **base)
    blob = "\n".join(evidence_texts)
    if not blob.strip():
        return VerificationItem(status="SKIPPED", verdict="UNVERIFIABLE", reason="evidence 원문이 비어 있습니다.", **base)
    matched = contains_any_date(blob, value) if kind == "date" else contains_text(blob, str(value))
    if kind == "grade":
        matched = compact(str(value)) in compact(blob) or f"{value}등급" in compact(blob)
    if matched:
        return VerificationItem(status="PASS", verdict="SUPPORTED", reason="evidence 원문에서 값이 확인되었습니다.", **base)
    return VerificationItem(
        status="FAIL",
        verdict="UNSUPPORTED",
        reason="evidence 원문에서 추출값을 확인하지 못했습니다.",
        **base,
    )


def evidence_texts(product: CanonicalProduct, refs: list[str], chunks: list[Chunk]) -> list[str]:
    texts: list[str] = []
    evidence_map = {item.chunk_id: item.source_text for item in product.evidence}
    chunk_map = {chunk.chunk_id: chunk.text for chunk in chunks}
    for ref in refs:
        text = evidence_map.get(ref) or chunk_map.get(ref)
        if text:
            texts.append(text)
    return texts
