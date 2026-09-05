"""Deterministic risk-table confidence scoring and selection policy.

HIGH confidence tables become the primary canonical risk source.
LLM / existing risks are supplement-only and must remain source-grounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from parsers.table_parser import (
    RISK_DESCRIPTION_COLUMN,
    RISK_NAME_COLUMN,
    is_semantic_risk_table,
    risk_column_roles,
)
from processing.risk_heading_anchor import is_generic_risk_heading
from processing.risk_row_extractor import (
    RiskCandidate,
    collect_table_risk_candidates,
    compact_risk_text,
    is_container_risk_heading,
)
from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable
from schemas.product import InvestmentRiskItem

ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW"]

_MIN_HIGH_VALID = 3
_MIN_MEDIUM_VALID = 2
_MIN_DESC_LEN = 20
_MAX_GENERIC_RATIO_HIGH = 0.15
_MAX_GENERIC_RATIO_MEDIUM = 0.35


@dataclass(frozen=True)
class ScoredTableRisk:
    candidate: RiskCandidate
    name_key: str
    valid: bool
    reasons: tuple[str, ...] = ()


@dataclass
class TableRiskAssessment:
    level: ConfidenceLevel
    score: float
    valid: list[ScoredTableRisk] = field(default_factory=list)
    rejected: list[ScoredTableRisk] = field(default_factory=list)
    semantic_table_count: int = 0
    reasons: list[str] = field(default_factory=list)


def name_key(text: str | None) -> str:
    return compact_risk_text(text).lower()


def description_overlap(left: str | None, right: str | None) -> float:
    a = compact_risk_text(left)
    b = compact_risk_text(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a[:500], b[:500]).ratio()


def _roles_for(table: DetectedTable) -> list[str]:
    return list(table.column_roles or risk_column_roles(table.headers) or [])


def _table_in_risk_section(table: DetectedTable, chunks: list[Chunk]) -> bool:
    for chunk in chunks:
        if chunk.table_id == table.table_id:
            if chunk.section_type == SectionType.INVESTMENT_RISK:
                return True
    # Fallback: semantic risk tables are treated as in-section when marked.
    return is_semantic_risk_table(table)


def score_candidate(candidate: RiskCandidate) -> ScoredTableRisk:
    name = (candidate.name or "").strip()
    desc = (candidate.description or "").strip()
    key = name_key(name)
    reasons: list[str] = []
    if not key:
        reasons.append("empty_name")
    if is_generic_risk_heading(name) or is_container_risk_heading(name):
        reasons.append("generic_or_container")
    if "위험" not in name and "리스크" not in name.upper():
        reasons.append("no_risk_token")
    if len(key) < 4 or len(key) > 48:
        reasons.append("name_length")
    if len(compact_risk_text(desc)) < _MIN_DESC_LEN:
        reasons.append("short_description")
    if not candidate.evidence_refs:
        reasons.append("missing_evidence")
    if candidate.row_index is None and candidate.table_id:
        reasons.append("missing_row_index")
    valid = not reasons or reasons == ["missing_row_index"]
    # row_index missing alone is soft — still valid if everything else passes
    if reasons == ["missing_row_index"]:
        valid = True
    if "generic_or_container" in reasons or "empty_name" in reasons or "no_risk_token" in reasons:
        valid = False
    if "short_description" in reasons or "missing_evidence" in reasons:
        valid = False
    return ScoredTableRisk(
        candidate=candidate,
        name_key=key,
        valid=valid,
        reasons=tuple(reasons),
    )


def assess_table_risk_confidence(
    chunks: list[Chunk],
    tables: list[DetectedTable] | None,
) -> TableRiskAssessment:
    risk_tables = [
        table
        for table in tables or []
        if is_semantic_risk_table(table) and _table_in_risk_section(table, chunks)
    ]
    raw = collect_table_risk_candidates(chunks, risk_tables or tables)
    scored = [score_candidate(item) for item in raw]
    valid = [item for item in scored if item.valid]
    rejected = [item for item in scored if not item.valid]
    generic_n = sum(
        1
        for item in scored
        if "generic_or_container" in item.reasons
    )
    generic_ratio = (generic_n / len(scored)) if scored else 1.0
    with_name_desc_roles = 0
    for table in risk_tables:
        roles = _roles_for(table)
        if RISK_NAME_COLUMN in roles and RISK_DESCRIPTION_COLUMN in roles:
            with_name_desc_roles += 1
    reasons: list[str] = []
    score = 0.0
    if risk_tables:
        score += 0.25
        reasons.append(f"semantic_tables={len(risk_tables)}")
    if with_name_desc_roles:
        score += 0.2
        reasons.append(f"name_desc_roles={with_name_desc_roles}")
    if len(valid) >= _MIN_HIGH_VALID:
        score += 0.3
    elif len(valid) >= _MIN_MEDIUM_VALID:
        score += 0.15
    reasons.append(f"valid_candidates={len(valid)}")
    if generic_ratio <= _MAX_GENERIC_RATIO_HIGH:
        score += 0.15
    elif generic_ratio <= _MAX_GENERIC_RATIO_MEDIUM:
        score += 0.05
    reasons.append(f"generic_ratio={generic_ratio:.2f}")
    evidenced = sum(1 for item in valid if item.candidate.evidence_refs)
    if valid and evidenced == len(valid):
        score += 0.1
        reasons.append("all_valid_evidenced")

    if (
        len(valid) >= _MIN_HIGH_VALID
        and risk_tables
        and with_name_desc_roles
        and generic_ratio <= _MAX_GENERIC_RATIO_HIGH
    ):
        level: ConfidenceLevel = "HIGH"
    elif len(valid) >= _MIN_MEDIUM_VALID and risk_tables and generic_ratio <= _MAX_GENERIC_RATIO_MEDIUM:
        level = "MEDIUM"
    else:
        level = "LOW"
    return TableRiskAssessment(
        level=level,
        score=round(score, 3),
        valid=valid,
        rejected=rejected,
        semantic_table_count=len(risk_tables),
        reasons=reasons,
    )


def candidate_to_risk_item(
    scored: ScoredTableRisk,
    *,
    normalize,
) -> InvestmentRiskItem | None:
    """Convert via caller's _normalize_risk for consistent hygiene."""
    cand = scored.candidate
    return normalize(cand.name, cand.description, list(cand.evidence_refs))


def _grounded_in_chunks(item: InvestmentRiskItem, chunks: list[Chunk]) -> bool:
    key = name_key(item.name)
    if not key:
        return False
    chunk_map = {chunk.chunk_id: chunk.text or "" for chunk in chunks}
    if item.evidence_refs:
        blob = " ".join(chunk_map.get(ref, "") for ref in item.evidence_refs)
        if key in compact_risk_text(blob):
            return True
    # Allow page-level grounding for supplements.
    for chunk in chunks:
        if key in compact_risk_text(chunk.text or ""):
            return True
    return False


def merge_risk_items(
    primary: list[InvestmentRiskItem],
    secondary: list[InvestmentRiskItem],
) -> list[InvestmentRiskItem]:
    """Union with deterministic dedup.

    Same normalized name + overlapping/contained description → merge.
    Same name + low description overlap → keep longer/table-first description
    (do not invent a second identical name entry).
    """
    merged: list[InvestmentRiskItem] = []
    index: dict[str, int] = {}

    def upsert(item: InvestmentRiskItem, *, prefer_incoming: bool = False) -> None:
        key = name_key(item.name)
        if not key:
            return
        if key not in index:
            index[key] = len(merged)
            merged.append(item)
            return
        idx = index[key]
        current = merged[idx]
        overlap = description_overlap(current.description, item.description)
        if overlap >= 0.55 or not (current.description and item.description):
            # merge refs + prefer richer description
            refs = list(dict.fromkeys([*(current.evidence_refs or []), *(item.evidence_refs or [])]))
            cur_desc = current.description or ""
            new_desc = item.description or ""
            if prefer_incoming and len(compact_risk_text(new_desc)) >= len(compact_risk_text(cur_desc)):
                desc = new_desc or cur_desc
                name = item.name or current.name
            elif len(compact_risk_text(cur_desc)) >= len(compact_risk_text(new_desc)):
                desc = cur_desc or new_desc
                name = current.name or item.name
            else:
                desc = new_desc or cur_desc
                name = item.name or current.name
            merged[idx] = InvestmentRiskItem(
                name=name,
                description=desc,
                evidence_refs=refs,
            )
            return
        # Low overlap, same name: keep richer description, still one record.
        if len(compact_risk_text(item.description)) > len(compact_risk_text(current.description)):
            merged[idx] = InvestmentRiskItem(
                name=item.name or current.name,
                description=item.description,
                evidence_refs=list(
                    dict.fromkeys([*(item.evidence_refs or []), *(current.evidence_refs or [])])
                ),
            )

    for item in primary:
        upsert(item, prefer_incoming=False)
    for item in secondary:
        upsert(item, prefer_incoming=False)
    return merged


def select_risks_with_table_policy(
    *,
    existing: list[InvestmentRiskItem],
    extracted: list[InvestmentRiskItem],
    chunks: list[Chunk],
    tables: list[DetectedTable] | None,
    normalize,
    should_preserve_existing,
) -> tuple[list[InvestmentRiskItem], TableRiskAssessment]:
    """Apply HIGH/MEDIUM/LOW table selection policy.

    - HIGH: table valid set is base; LLM/existing may supplement only if grounded
      and not already represented.
    - MEDIUM: union(table, grounded existing/extracted) + dedup
    - LOW: legacy preserve / extracted / existing fallback
    """
    assessment = assess_table_risk_confidence(chunks, tables)
    table_items: list[InvestmentRiskItem] = []
    for scored in assessment.valid:
        item = candidate_to_risk_item(scored, normalize=normalize)
        if item is not None:
            table_items.append(item)
    table_items = merge_risk_items(table_items, [])

    if assessment.level == "HIGH" and table_items:
        # Table is authoritative. Do not merge LLM supplements — they introduce
        # non-deterministic A/B drift across force re-extractions.
        return _renormalize_items(table_items, normalize), assessment

    if assessment.level == "MEDIUM" and table_items:
        grounded_existing = [
            item
            for item in existing
            if _grounded_in_chunks(item, chunks)
            and not is_generic_risk_heading(item.name)
            and not is_container_risk_heading(item.name)
        ]
        grounded_extracted = [
            item
            for item in extracted
            if _grounded_in_chunks(item, chunks)
            and not is_generic_risk_heading(item.name)
            and not is_container_risk_heading(item.name)
        ]
        selected = merge_risk_items(
            table_items, merge_risk_items(grounded_existing, grounded_extracted)
        )
        return _renormalize_items(selected, normalize), assessment

    # LOW / no usable table
    if should_preserve_existing(existing):
        return _renormalize_items(existing, normalize), assessment
    if extracted:
        return _renormalize_items(extracted, normalize), assessment
    return _renormalize_items(existing, normalize), assessment


def _renormalize_items(items: list[InvestmentRiskItem], normalize) -> list[InvestmentRiskItem]:
    """Force a single name/description hygiene pass for stable A/B output."""
    out: list[InvestmentRiskItem] = []
    seen: set[str] = set()
    for item in items:
        normalized = normalize(item.name, item.description, list(item.evidence_refs or []))
        chosen = normalized or item
        key = name_key(chosen.name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(chosen)
    return out
