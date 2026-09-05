from __future__ import annotations

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from schemas.chunk import Chunk, SectionType
from schemas.product import CanonicalProduct, VerificationItem
from processing.narrative_extractor import (is_complete_narrative, is_garbage_narrative, is_strategy_disclaimer, is_semantic_risk_description)
from verification.text import compact, contains_text, format_value, token_overlap

NARRATIVE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "당신은 추출 JSON 값이 제공된 원문 evidence만으로 지지되는지 판정합니다. "
            "외부 금융지식을 쓰지 않습니다. 원문에 없는 내용을 추론하지 않습니다. "
            "JSON만 반환합니다: "
            '{{"verdict":"SUPPORTED|PARTIALLY_SUPPORTED|UNSUPPORTED|CONTRADICTED","reason":"..."}}',
        ),
        (
            "human",
            "필드: {field_path}\n추출값:\n{extracted}\n\n원문 evidence:\n{evidence}\n\nJSON만 반환하십시오.",
        ),
    ]
)

VERDICT_STATUS = {
    "SUPPORTED": "PASS",
    "PARTIALLY_SUPPORTED": "WARNING",
    "UNSUPPORTED": "FAIL",
    "CONTRADICTED": "FAIL",
}


def verify_narrative(
    field_path: str,
    value: str | None,
    evidence_texts: list[str],
    evidence_refs: list[str],
    llm: BaseChatModel | None = None,
    chunks: list[Chunk] | None = None,
    product: CanonicalProduct | None = None,
    role: str | None = None,
    sibling_text: str | None = None,
    sibling_refs: list[str] | None = None,
    risk_name: str | None = None,
) -> VerificationItem:
    base = {
        "field_path": field_path,
        "extracted_value": format_value(value),
        "evidence_refs": list(evidence_refs),
    }
    if not (value or "").strip():
        return VerificationItem(status="SKIPPED", verdict="NOT_APPLICABLE", method="llm_semantic", reason="추출값이 비어 있습니다.", **base)
    if role in {"objective", "strategy"}:
        if is_garbage_narrative(value):
            return VerificationItem(
                status="FAIL",
                verdict="UNSUPPORTED",
                method="deterministic",
                reason="추출문이 면책 문구 또는 제목 조각입니다.",
                **base,
            )
        if not is_complete_narrative(value, role):
            return VerificationItem(
                status="WARNING",
                verdict="PARTIALLY_SUPPORTED",
                method="deterministic",
                reason="추출문이 투자목적/전략 본문으로 완결되지 않았습니다.",
                **base,
            )
        if role == "strategy" and is_strategy_disclaimer(value):
            return VerificationItem(
                status="FAIL",
                verdict="ROLE_MISMATCH",
                method="deterministic",
                reason="성과 비보장/면책 문구로 실제 투자전략을 설명하지 않습니다.",
                **base,
            )
    if not evidence_refs:
        return VerificationItem(status="SKIPPED", verdict="UNVERIFIABLE", method="llm_semantic", reason="evidence_ref가 없습니다.", **base)
    evidence = "\n\n".join(text for text in evidence_texts if text)
    if not evidence.strip():
        return VerificationItem(status="SKIPPED", verdict="UNVERIFIABLE", method="llm_semantic", reason="evidence 원문이 비어 있습니다.", **base)

    lexical = _lexical_verdict(value, evidence)
    if role == "risk" and lexical[0] == "SUPPORTED" and not is_semantic_risk_description(risk_name or field_path, value):
        return VerificationItem(
            status="WARNING",
            verdict="ROLE_MISMATCH",
            method="deterministic",
            reason="원문에는 존재하지만 실제 투자위험 메커니즘 설명으로 보기 어렵습니다.",
            **base,
        )
    if llm is None or lexical[0] in {"SUPPORTED", "CONTRADICTED"}:
        verdict, reason = lexical
        item = VerificationItem(
            status=VERDICT_STATUS[verdict],
            verdict=verdict,
            method="deterministic",
            reason=reason,
            **base,
        )
        return _apply_role_check(
            item,
            value=value,
            evidence=evidence,
            evidence_refs=evidence_refs,
            chunks=chunks or [],
            product=product,
            role=role,
            sibling_text=sibling_text,
            sibling_refs=sibling_refs or [],
        )
    try:
        messages = NARRATIVE_PROMPT.invoke(
            {"field_path": field_path, "extracted": value, "evidence": evidence[:4000]}
        )
        raw_msg = llm.invoke(messages)
        text = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        raw = json.loads(cleaned)
        verdict = str((raw or {}).get("verdict") or "").upper()
        reason = str((raw or {}).get("reason") or "").strip() or "LLM semantic verification"
        if verdict not in VERDICT_STATUS:
            raise ValueError(f"unknown verdict: {verdict}")
        item = VerificationItem(
            status=VERDICT_STATUS[verdict],
            verdict=verdict,
            method="llm_semantic",
            reason=reason,
            **base,
        )
        return _apply_role_check(
            item,
            value=value,
            evidence=evidence,
            evidence_refs=evidence_refs,
            chunks=chunks or [],
            product=product,
            role=role,
            sibling_text=sibling_text,
            sibling_refs=sibling_refs or [],
        )
    except Exception as exc:
        return VerificationItem(
            status="WARNING",
            verdict="UNVERIFIABLE",
            method="llm_semantic",
            reason=f"LLM verifier 호출 실패: {exc}",
            **base,
        )


def _lexical_verdict(value: str, evidence: str) -> tuple[str, str]:
    if contains_text(evidence, value):
        return "SUPPORTED", "추출문이 evidence 원문에 포함됩니다."
    if _contradicted(value, evidence):
        return "CONTRADICTED", "추출문이 evidence 원문과 명백히 모순됩니다."
    overlap = token_overlap(value, evidence)
    if overlap >= 0.75:
        return "SUPPORTED", f"evidence 부분 문자열 일치율 {overlap:.2f}"
    if overlap >= 0.35:
        return "PARTIALLY_SUPPORTED", f"evidence가 추출문의 일부만 지지합니다 (일치율 {overlap:.2f})"
    return "UNSUPPORTED", f"evidence가 추출문을 지지하지 않습니다 (일치율 {overlap:.2f})"


def _contradicted(value: str, evidence: str) -> bool:
    left = compact(value)
    right = compact(evidence)
    pairs = (
        ("보장하지않", "보장합니다"),
        ("원본을보장하지", "원본을보장"),
        ("투자하지않", "투자합니다"),
    )
    for negative, positive in pairs:
        if negative in left and positive in right and negative not in right:
            return True
        if negative in right and positive in left and negative not in left:
            return True
    return False


def _apply_role_check(
    item: VerificationItem,
    value: str | None,
    evidence: str,
    evidence_refs: list[str],
    chunks: list[Chunk],
    product: CanonicalProduct | None,
    role: str | None,
    sibling_text: str | None,
    sibling_refs: list[str],
) -> VerificationItem:
    if item.status != "PASS" or role not in {"objective", "strategy"}:
        return item
    if compact(value) and compact(sibling_text) and compact(value) == compact(sibling_text):
        item.status = "WARNING"
        item.verdict = "ROLE_MISMATCH"
        if evidence_refs and set(evidence_refs) == set(sibling_refs):
            item.reason = "investment_objective와 investment_strategy가 동일 문장이고 같은 evidence를 참조합니다."
        else:
            item.reason = "investment_objective와 investment_strategy에 동일한 문장이 들어 있습니다."
        return item
    if _combined_purpose_heading(evidence):
        return item
    sections = _evidence_sections(evidence_refs, chunks, product)
    if role == "objective" and sections and sections <= {SectionType.INVESTMENT_STRATEGY}:
        item.status = "WARNING"
        item.verdict = "ROLE_MISMATCH"
        item.reason = "objective가 INVESTMENT_STRATEGY 구간 evidence만 참조합니다."
    elif role == "strategy" and sections and sections <= {SectionType.INVESTMENT_OBJECTIVE}:
        item.status = "WARNING"
        item.verdict = "ROLE_MISMATCH"
        item.reason = "strategy가 INVESTMENT_OBJECTIVE 구간 evidence만 참조합니다."
    return item


def _evidence_sections(
    refs: list[str],
    chunks: list[Chunk],
    product: CanonicalProduct | None,
) -> set[SectionType]:
    found: set[SectionType] = set()
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    evidence_map = {item.chunk_id: item.section_type for item in product.evidence} if product else {}
    for ref in refs:
        chunk = chunk_map.get(ref)
        if chunk:
            found.add(chunk.section_type)
            continue
        raw = evidence_map.get(ref)
        if raw:
            try:
                found.add(SectionType(raw))
            except ValueError:
                continue
    return found


def _combined_purpose_heading(evidence: str) -> bool:
    head = compact(evidence)[:80]
    return "투자목적및투자전략" in head or ("투자목적" in head and "투자전략" in head)
