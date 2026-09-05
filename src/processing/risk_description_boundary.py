"""Description record-boundary clipping with Hard / Conditional / Soft stops.

Does not create, drop, or rename risk records — description text only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from processing.risk_semantic_role_classifier import RiskSemanticRoleClassifier, compact_semantic_text
from schemas.risk_extraction import RiskSemanticRole


class StopKind(StrEnum):
    HARD = "HARD"
    CONDITIONAL = "CONDITIONAL"
    SOFT = "SOFT"


@dataclass(frozen=True)
class BoundaryHit:
    kind: StopKind
    offset: int
    reason: str


@dataclass
class ProvenanceContext:
    """Optional layout/provenance signals for the current risk description."""

    table_id: str | None = None
    section_id: str | None = None
    row_index: int | None = None
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    # Ordered risk names that follow this row in the same table/structure.
    next_row_names: list[str] = field(default_factory=list)
    next_row_bbox: tuple[float, float, float, float] | None = None
    # Evidence chunk ids for the current binding (for page/table continuity).
    evidence_refs: list[str] = field(default_factory=list)
    # Pages already accepted for this description (structural continuation).
    accepted_pages: list[int] = field(default_factory=list)


# Keyword blacklist is an *auxiliary* signal only (never sole Hard Stop).
_AUX_SECTION_KEYWORDS = (
    "매입방법",
    "환매방법",
    "과세",
    "전환절차",
    "투자결정시유의사항",
    "이집합투자기구에적합한",
    "기준가격산정",
    "기준가의산정",
    "기준가격의산정",
)

_HARD_ROLES = {
    RiskSemanticRole.RISK_CATEGORY,
    RiskSemanticRole.SECTION_HEADING,
    RiskSemanticRole.TABLE_HEADER,
}

_NAME_LINE_RE = re.compile(
    r"(?:^|\n)\s*([가-힣A-Za-z0-9 ·ㆍ()\-]{2,40}?위험)\s*(?:\n|$)"
)
_CATEGORY_LINE_RE = re.compile(
    r"(?:^|\n)\s*((?:[가-라]\.\s*)?(?:일반위험|특수위험|기타투자위험|기타위험)(?:등)?)\s*(?:\n|$)"
)
_NUMBERED_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(\d+(?:의\d+)?(?:\.\s*)?(?:집합투자기구의)?투자위험[^\n]{0,20})\s*(?:\n|$)"
)
_TABLE_HEADER_LINE_RE = re.compile(
    r"(?:^|\n)\s*((?:구\s*분|세부\s*구분|위험\s*구\s*분|투자위험\s*구\s*분|위험\s*명|"
    r"투자위험\s*명|항\s*목|주요\s*투자위험|투자위험의\s*주요내용))\s*(?:\n|$)"
)
# Soft: trailing punctuation / emphasis markers that are layout residue.
_SOFT_PUNCT_TAIL_RE = re.compile(r"(?:[*※◆■▶]|[-–—·ㆍ]{2,})\s*$")


def compact_text(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _compact_map(text: str) -> list[int]:
    return [index for index, char in enumerate(text) if not char.isspace()]


def _names_related(current: str, candidate: str) -> bool:
    a = compact_semantic_text(current)
    b = compact_semantic_text(candidate)
    if not a or not b or a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return abs(len(a) - len(b)) <= 2
    return False


def _line_start_layout(text: str, offset: int) -> bool:
    """True when offset is at a line boundary (layout signal)."""
    if offset <= 0:
        return True
    # Walk back over spaces on the same visual line.
    cursor = offset
    while cursor > 0 and text[cursor - 1] in " \t":
        cursor -= 1
    return cursor == 0 or text[cursor - 1] == "\n"


def _bbox_below(
    current: tuple[float, float, float, float] | None,
    nxt: tuple[float, float, float, float] | None,
) -> bool:
    if not current or not nxt:
        return False
    # next top roughly at/below current bottom (same column band optional).
    return nxt[1] >= current[1] - 1.0


def _page_from_ref(ref: str) -> int | None:
    match = re.search(r"_p(\d+)_", ref or "")
    return int(match.group(1)) if match else None


def _table_from_ref(ref: str) -> str | None:
    match = re.search(r"_(t\d+)\b", ref or "", flags=re.IGNORECASE)
    return match.group(1).lower() if match else None


def _has_structural_page_continuation(
    provenance: ProvenanceContext | None,
    remainder: str,
    offset: int,
) -> bool:
    """Allow page transition only with structural continuation evidence."""
    if provenance is None:
        return False
    # Same table continuing across pages.
    if provenance.table_id:
        return True
    # Mid-sentence / connector before the page break region.
    head = compact_text(remainder[:offset])
    if head.endswith(("또한", "그리고", "이에", "따라", "경우", "수있으며", "수있습니다")):
        return True
    if re.search(r"[가-힣A-Za-z0-9]$", remainder[:offset] or ""):
        # Ends mid-word/token without terminal punctuation → likely wrap.
        tail = remainder[:offset].rstrip()
        if tail and tail[-1] not in ".。!?！？":
            return True
    return False


def find_hard_stops(
    remainder: str,
    *,
    current_name: str,
    provenance: ProvenanceContext | None = None,
    classifier: RiskSemanticRoleClassifier | None = None,
) -> list[BoundaryHit]:
    """Hard stops: next risk row coords / semantic header roles."""
    classifier = classifier or RiskSemanticRoleClassifier()
    hits: list[BoundaryHit] = []
    compact_current = compact_semantic_text(current_name)

    # 1) Next risk row — prefer explicit sibling row names (coordinate/order based).
    for sibling in provenance.next_row_names if provenance else []:
        if not sibling.strip() or _names_related(current_name, sibling):
            continue
        line_match = re.search(
            rf"(?:^|\n)\s*{re.escape(sibling.strip())}\s*(?:\n|$)",
            remainder,
        )
        if line_match and line_match.start() > 0:
            hits.append(BoundaryHit(StopKind.HARD, line_match.start(), "next_risk_row"))
            continue
        if (
            provenance
            and provenance.next_row_bbox
            and _bbox_below(provenance.bbox, provenance.next_row_bbox)
        ):
            plain = re.search(re.escape(sibling.strip()), remainder)
            if plain and plain.start() > 0:
                hits.append(
                    BoundaryHit(StopKind.HARD, plain.start(), "next_risk_row_bbox")
                )
                continue
        # Flattened table rows: sibling name as a compact token after content.
        sibling_compact = compact_text(sibling)
        if not sibling_compact:
            continue
        spaced = r"\s*".join(map(re.escape, sibling_compact))
        flat = re.search(
            rf"(?<![가-힣A-Za-z0-9]){spaced}(?![가-힣A-Za-z0-9])",
            remainder,
        )
        if flat and flat.start() > 0:
            hits.append(
                BoundaryHit(StopKind.HARD, flat.start(), "next_risk_row_flat")
            )

    # 1b) RISK_NAME line headers (layout line-start).
    for match in _NAME_LINE_RE.finditer(remainder):
        candidate = match.group(1)
        if _names_related(current_name, candidate):
            continue
        role = classifier.classify(candidate)
        if role != RiskSemanticRole.RISK_NAME:
            continue
        if match.start() <= 0:
            continue
        hits.append(BoundaryHit(StopKind.HARD, match.start(), "next_risk_name_line"))

    # 2) RISK_CATEGORY / SECTION_HEADING / TABLE_HEADER
    for match in _CATEGORY_LINE_RE.finditer(remainder):
        if match.start() <= 0:
            continue
        role = classifier.classify(match.group(1))
        if role in _HARD_ROLES or role == RiskSemanticRole.RISK_CATEGORY:
            hits.append(BoundaryHit(StopKind.HARD, match.start(), "risk_category"))

    for match in _NUMBERED_SECTION_RE.finditer(remainder):
        if match.start() <= 0:
            continue
        role = classifier.classify(match.group(1))
        if role in _HARD_ROLES or role == RiskSemanticRole.SECTION_HEADING:
            hits.append(BoundaryHit(StopKind.HARD, match.start(), "section_heading"))

    for match in _TABLE_HEADER_LINE_RE.finditer(remainder):
        if match.start() <= 0:
            continue
        hits.append(BoundaryHit(StopKind.HARD, match.start(), "table_header"))

    # Generic classifier pass on short newline-bounded lines.
    for match in re.finditer(r"(?:^|\n)\s*([^\n]{2,40})\s*(?:\n|$)", remainder):
        if match.start() <= 0:
            continue
        line = match.group(1).strip()
        role = classifier.classify(line)
        if role in _HARD_ROLES:
            # Avoid cutting on the current name if re-detected.
            if _names_related(current_name, line):
                continue
            hits.append(
                BoundaryHit(StopKind.HARD, match.start(), f"role:{role.value}")
            )

    return hits


def find_conditional_stops(
    remainder: str,
    *,
    current_name: str,
    provenance: ProvenanceContext | None = None,
    classifier: RiskSemanticRoleClassifier | None = None,
) -> list[BoundaryHit]:
    """Conditional stops: provenance breaks, page transitions, aux keywords+layout."""
    classifier = classifier or RiskSemanticRoleClassifier()
    hits: list[BoundaryHit] = []

    # 3) Provenance continuity — page/table markers embedded in evidence text.
    for match in re.finditer(
        r"(?:^|\n)\s*(?:\[TABLE_ID:[^\]]+\]|\(page=\d+,\s*section=[A-Z_]+\))",
        remainder,
    ):
        if match.start() <= 0:
            continue
        # Marker alone is not enough; require discontinuity vs current provenance.
        marker = match.group(0)
        discontinuous = False
        table_match = re.search(r"TABLE_ID:([^\]\s]+)", marker)
        page_match = re.search(r"page=(\d+)", marker)
        section_match = re.search(r"section=([A-Z_]+)", marker)
        if provenance:
            if table_match and provenance.table_id and table_match.group(1) != provenance.table_id:
                discontinuous = True
            if section_match and provenance.section_id and section_match.group(1) != provenance.section_id:
                discontinuous = True
            if page_match:
                page = int(page_match.group(1))
                if provenance.page_number and page != provenance.page_number:
                    if not _has_structural_page_continuation(provenance, remainder, match.start()):
                        discontinuous = True
        else:
            # Without provenance, embedded table/section markers still signal a boundary
            # when they appear mid-body after content.
            discontinuous = True
        if discontinuous:
            hits.append(
                BoundaryHit(StopKind.CONDITIONAL, match.start(), "provenance_break")
            )

    # 4) Page transition via form-feed / explicit page banners without continuation.
    for match in re.finditer(r"(?:\f|(?:^|\n)\s*-\s*\d+\s*-\s*(?:\n|$))", remainder):
        if match.start() <= 0:
            continue
        if not _has_structural_page_continuation(provenance, remainder, match.start()):
            hits.append(
                BoundaryHit(StopKind.CONDITIONAL, match.start(), "page_transition")
            )

    # 5) Keyword blacklist — only with layout (line-start) + non-description role.
    compact_current = compact_text(current_name)
    for keyword in _AUX_SECTION_KEYWORDS:
        if keyword in compact_current:
            continue
        # Allow spaced variants in raw text.
        pattern = r"\s*".join(map(re.escape, keyword))
        for match in re.finditer(rf"(?:^|\n)\s*({pattern})\s*(?:\n|$)", remainder):
            if match.start() <= 0:
                continue
            if not _line_start_layout(remainder, match.start()):
                continue
            role = classifier.classify(re.sub(r"\s+", "", match.group(1)))
            if role in _HARD_ROLES or role == RiskSemanticRole.OTHER:
                # OTHER at line-start with blacklist = conditional section-like stop.
                hits.append(
                    BoundaryHit(
                        StopKind.CONDITIONAL,
                        match.start(),
                        f"aux_keyword:{keyword}",
                    )
                )
        # Inline (no newline) only if flanked by non-letter and layout-like.
        for match in re.finditer(
            rf"(?<![가-힣A-Za-z0-9])({pattern})(?![가-힣A-Za-z0-9])",
            remainder,
        ):
            if match.start() <= 0:
                continue
            # Require preceding sentence end or pipe/table glue as layout cue.
            prev = remainder[max(0, match.start() - 3) : match.start()]
            if not re.search(r"[.\n|]|다\s*$", prev):
                continue
            hits.append(
                BoundaryHit(
                    StopKind.CONDITIONAL,
                    match.start(),
                    f"aux_keyword_inline:{keyword}",
                )
            )

    return hits


def find_soft_stops(remainder: str) -> list[BoundaryHit]:
    """Soft stops: bold/punctuation — weakest, last resort only."""
    hits: list[BoundaryHit] = []
    # Emphasis bullets that often start a new block after a description.
    for match in re.finditer(r"(?:^|\n)\s*[※*◆■▶▷●○]\s+\S", remainder):
        if match.start() > 0:
            hits.append(BoundaryHit(StopKind.SOFT, match.start(), "emphasis_bullet"))
    # Double punctuation run mid-body (rare layout glue).
    for match in _SOFT_PUNCT_TAIL_RE.finditer(remainder):
        if 0 < match.start() < len(remainder):
            hits.append(BoundaryHit(StopKind.SOFT, match.start(), "punct_tail"))
    return hits


def choose_boundary(
    remainder: str,
    *,
    current_name: str,
    provenance: ProvenanceContext | None = None,
    classifier: RiskSemanticRoleClassifier | None = None,
    allow_soft: bool = False,
) -> BoundaryHit | None:
    """Prefer Hard, then Conditional; Soft only when allow_soft and nothing else."""
    classifier = classifier or RiskSemanticRoleClassifier()
    hard = find_hard_stops(
        remainder, current_name=current_name, provenance=provenance, classifier=classifier
    )
    if hard:
        return min(hard, key=lambda hit: hit.offset)
    conditional = find_conditional_stops(
        remainder, current_name=current_name, provenance=provenance, classifier=classifier
    )
    if conditional:
        return min(conditional, key=lambda hit: hit.offset)
    if allow_soft:
        soft = find_soft_stops(remainder)
        if soft:
            return min(soft, key=lambda hit: hit.offset)
    return None


def clip_description_body(
    name: str,
    evidence_text: str | None,
    *,
    provenance: ProvenanceContext | None = None,
    classifier: RiskSemanticRoleClassifier | None = None,
    allow_soft: bool = False,
) -> str | None:
    """Keep body between this risk name and the earliest Hard/Conditional stop."""
    if not evidence_text or not name:
        return None
    compact_name = compact_text(name)
    if not compact_name:
        return None
    compact_evidence = compact_text(evidence_text)
    start = compact_evidence.find(compact_name)
    if start < 0:
        return None
    mapping = _compact_map(evidence_text)
    body_compact_start = start + len(compact_name)
    if body_compact_start >= len(mapping):
        return None
    raw_body_start = mapping[body_compact_start]
    remainder = evidence_text[raw_body_start:]
    hit = choose_boundary(
        remainder,
        current_name=name,
        provenance=provenance,
        classifier=classifier,
        allow_soft=allow_soft,
    )
    body = remainder[: hit.offset] if hit is not None else remainder
    body = re.sub(r"\s+", " ", body).strip(" |-·ㆍ,，;；:：")
    return body or None


def soft_cleanup_description(text: str | None) -> str:
    """Soft cleanup only: delimiter / header residue. No semantic clipping."""
    if not text:
        return ""
    cleaned = re.sub(r"\[TABLE_ID:[^\]]+\]", " ", text)
    cleaned = re.sub(r"\(page=\d+,\s*section=[A-Z_]+\)", " ", cleaned)
    cleaned = re.sub(r"(?:\s*\|\s*)+$", "", cleaned)
    cleaned = re.sub(r"\s*\|\s*", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" |-·ㆍ,，;；:：")
    return cleaned
