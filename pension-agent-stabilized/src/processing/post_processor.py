import re

from processing.class_candidates import (
    class_code,
    class_identity,
    harvest_class_candidates,
    is_plausible_class_name,
    normalize_class_name,
    prefer_class_name,
)
from processing.class_resolver import ClassResolver
from processing.narrative_extractor import apply_narrative_facts
from processing.metadata_extractor import apply_metadata_facts
from processing.table_extractor import apply_table_facts
from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable
from schemas.product import CandidateOutcome, CanonicalProduct, OwnershipOutcome, ProductClass

STALE_WARNING_MARKERS = (
    "FeeExtractionChain",
    "PerformanceExtractionChain",
    "표가 잘려",
    "CHUNK_ID에 없어",
    "판매수수료율이 표에",
    "동종유형 총보수·비용 값이 없는 클래스",
    "판매수수료가 '없음'",
    "판매보수는 표에",
    "Investment objective text is truncated",
    "Investment objective text appears truncated",
    "Investment strategy text is truncated",
    "Investment strategy text appears truncated",
)
FIELD_NAME_ALIASES = {
    "investment_objective": ("investment_objective", "product.investment_objective", "investment_objective.text"),
    "investment_strategy": ("investment_strategy", "product.investment_strategy", "investment_strategy.text"),
    "classes": ("classes",),
    "fees": ("fees",),
    "performance": ("performance",),
    "aum": ("aum",),
    "investment_risks": ("investment_risks",),
}


class PostProcessor:
    """Deterministic finalization after merge, before validation."""

    def process(
        self,
        product: CanonicalProduct,
        chunks: list[Chunk] | None = None,
        tables: list[DetectedTable] | None = None,
    ) -> CanonicalProduct:
        product = apply_metadata_facts(product, chunks)
        product = apply_table_facts(product, tables, chunks)
        product = apply_narrative_facts(product, chunks, tables)
        _drop_cost_example_fees(product)
        _cleanup_risk_description_artifacts(product)
        _rebind_cross_row_risk_descriptions(product)
        _normalize_asset_type(product)

        ordered: list[str] = []
        identity_index: dict[str, int] = {}
        refs: dict[str, list[str]] = {}
        class_resolver = ClassResolver(chunks, tables)

        def add(raw: str | None, evidence: list[str] | None = None) -> None:
            name = normalize_class_name(raw)
            if not is_plausible_class_name(name):
                # Single-share prospectuses use 투자신탁 as the sole fee/class subject.
                if re.sub(r"\s+", "", name or "") != "투자신탁":
                    return
                name = "투자신탁"
            ident = class_identity(name) if name != "투자신탁" else "투자신탁"
            evidence = list(evidence or [])
            if ident in identity_index:
                idx = identity_index[ident]
                current = ordered[idx]
                preferred = prefer_class_name(current, name) or current
                if preferred != current:
                    ordered[idx] = preferred
                    refs[preferred] = list(dict.fromkeys(refs.pop(current, []) + evidence))
                else:
                    refs[current] = list(dict.fromkeys(refs.get(current, []) + evidence))
                return
            identity_index[ident] = len(ordered)
            ordered.append(name)
            refs[name] = evidence

        for fee in product.fees:
            fee.class_name = normalize_class_name(fee.class_name)
            if fee.class_name and not is_plausible_class_name(fee.class_name):
                fee.class_name = class_resolver.resolve(fee.class_name) or fee.class_name
            add(fee.class_name, fee.evidence_refs)
        for row in product.performance:
            if row.class_name:
                row.class_name = normalize_class_name(row.class_name)
                if not is_plausible_class_name(row.class_name):
                    row.class_name = class_resolver.resolve(row.class_name) or row.class_name
            add(row.class_name, row.evidence_refs)
        for item in product.classes:
            if _keep_extra_class(item.class_name, identity_index) and item.evidence_refs:
                add(item.class_name, item.evidence_refs)
        for name in harvest_class_candidates(chunks, tables):
            if _keep_extra_class(name, identity_index):
                evidence = _class_evidence_refs(name, chunks)
                # source evidence 없는 보조 class 후보는 canonical final에 승격하지 않는다.
                if evidence:
                    add(name, evidence)

        product.classes = [
            ProductClass(class_name=name, evidence_refs=refs.get(name, [])) for name in ordered
        ]
        # Keep row-level source spelling on fee/performance facts. Referential
        # identity is resolved by class code in SchemaMapper; rewriting the row
        # to a longer label from another page breaks source grounding.
        _record_class_outcomes(product)
        product.extraction.missing_fields = []
        product.extraction.warnings = reconcile_extraction_warnings(product)
        _append_narrative_near_duplicate_warning(product)
        return product



def _drop_cost_example_fees(product: CanonicalProduct) -> None:
    """Drop KRW-thousand cost-example cells mistaken for % fee rates."""
    product.fees = [
        fee
        for fee in product.fees
        if fee.rate is None or fee.unit != "%" or fee.rate < 10
    ]


def _cleanup_risk_description_artifacts(product: CanonicalProduct) -> None:
    """Soft cleanup only: delimiter/header residue. No semantic clipping."""
    from processing.risk_description_boundary import soft_cleanup_description

    for risk in product.product.investment_risks:
        text = (risk.description or "").strip()
        if not text:
            continue
        cleaned = soft_cleanup_description(text)
        if cleaned:
            risk.description = cleaned


def _rebind_cross_row_risk_descriptions(product: CanonicalProduct) -> None:
    """Clip descriptions that spilled past Hard/Conditional record boundaries.

    Selection and count are untouched — description text only.
    """
    from processing.risk_description_boundary import ProvenanceContext, soft_cleanup_description
    from processing.risk_evidence_binder import RiskEvidenceBinder

    risks = product.product.investment_risks
    if not risks:
        return
    binder = RiskEvidenceBinder()
    evidence_map = {
        item.chunk_id: item.source_text or "" for item in product.evidence
    }
    evidence_meta = {item.chunk_id: item for item in product.evidence}
    sibling_names = [
        (risk.name or "").strip()
        for risk in risks
        if (risk.name or "").strip()
    ]

    def _page_from_ref(ref: str) -> int | None:
        match = re.search(r"_p(\d+)_", ref or "")
        return int(match.group(1)) if match else None

    def _table_from_ref(ref: str) -> str | None:
        match = re.search(r"_(t\d+)\b", ref or "", flags=re.IGNORECASE)
        return match.group(1).lower() if match else None

    for index, risk in enumerate(risks):
        name = (risk.name or "").strip()
        description = (risk.description or "").strip()
        if not name or not description:
            continue
        compact_desc = re.sub(r"\s+", "", description)
        compact_name = re.sub(r"\s+", "", name)

        refs = list(risk.evidence_refs or [])
        evidence = "".join(evidence_map.get(ref, "") for ref in refs)
        compact_evidence = re.sub(r"\s+", "", evidence)
        evidence_mismatch = bool(
            not evidence
            or compact_name not in compact_evidence
            or compact_desc not in compact_evidence
        )

        better_refs = list(refs)
        if evidence_mismatch:
            name_hits = [
                item.chunk_id
                for item in product.evidence
                if compact_name in re.sub(r"\s+", "", item.source_text or "")
            ]
            if name_hits:
                better_refs = list(dict.fromkeys(name_hits))
                evidence = "".join(evidence_map.get(ref, "") for ref in better_refs)
                compact_evidence = re.sub(r"\s+", "", evidence)

        # Build provenance from evidence chunks + subsequent sibling risk names.
        pages = [
            page
            for page in (_page_from_ref(ref) for ref in better_refs)
            if page is not None
        ]
        tables = [
            table_id
            for table_id in (_table_from_ref(ref) for ref in better_refs)
            if table_id
        ]
        section_ids = []
        for ref in better_refs:
            meta = evidence_meta.get(ref)
            if meta is not None and meta.section_type is not None:
                section_ids.append(
                    meta.section_type.value
                    if hasattr(meta.section_type, "value")
                    else str(meta.section_type)
                )
        next_names = [item for item in sibling_names[index + 1 :] if item]
        # Also include any sibling that appears after this name in the name list
        # (order may not match table order; still useful Hard Stop signal).
        for sibling in sibling_names:
            if sibling != name and sibling not in next_names:
                next_names.append(sibling)

        provenance = ProvenanceContext(
            table_id=tables[0] if tables else None,
            section_id=section_ids[0] if section_ids else None,
            row_index=None,
            page_number=pages[0] if pages else None,
            next_row_names=next_names,
            evidence_refs=better_refs,
            accepted_pages=list(dict.fromkeys(pages)),
        )

        spill_markers = any(
            sibling
            and re.sub(r"\s+", "", sibling) != compact_name
            and re.sub(r"\s+", "", sibling) in compact_desc
            and not compact_desc.startswith(re.sub(r"\s+", "", sibling))
            for sibling in sibling_names
        )
        if not spill_markers and not evidence_mismatch:
            if evidence and compact_desc in compact_evidence:
                # Still apply soft residue cleanup only.
                cleaned = soft_cleanup_description(description)
                if cleaned and cleaned != description:
                    risk.description = cleaned
                continue

        clipped = (
            binder.description_from_evidence_span(
                name, evidence, provenance=provenance
            )
            if evidence
            else None
        )
        page_text = "\n".join(
            item.source_text or ""
            for item in product.evidence
            if any(f"_p{page}_" in item.chunk_id for page in pages)
        )
        if not clipped and page_text:
            clipped = binder.description_from_evidence_span(
                name, page_text, provenance=provenance
            )
        candidate = soft_cleanup_description(clipped or description)
        if not candidate:
            continue
        compact_candidate = re.sub(r"\s+", "", candidate)
        grounded = (
            (evidence and compact_candidate in compact_evidence)
            or (page_text and compact_candidate in re.sub(r"\s+", "", page_text))
        )
        if compact_candidate == compact_desc and better_refs == refs:
            continue
        # Reject candidates that still contain a later sibling risk name.
        if any(
            sibling
            and re.sub(r"\s+", "", sibling) != compact_name
            and re.sub(r"\s+", "", sibling) in compact_candidate
            and not compact_candidate.startswith(re.sub(r"\s+", "", sibling))
            for sibling in sibling_names
        ):
            continue
        if evidence_mismatch:
            if not grounded:
                if compact_candidate not in compact_desc and not compact_desc.startswith(
                    compact_candidate
                ):
                    continue
        else:
            if len(compact_candidate) > len(compact_desc):
                continue
            if evidence and compact_candidate not in compact_evidence:
                if page_text and compact_candidate not in re.sub(r"\s+", "", page_text):
                    continue
        risk.description = candidate
        if better_refs:
            risk.evidence_refs = better_refs


def _normalize_asset_type(product: CanonicalProduct) -> None:
    """Normalize a source-supported asset type without an LLM call."""
    current = (product.product.asset_type or "").strip()
    compact_current = re.sub(r"\s+", "", current)
    aliases = {
        "주식": "주식형", "주식형": "주식형",
        "채권": "채권형", "채권형": "채권형",
        "혼합": "혼합형", "혼합형": "혼합형",
        "재간접": "재간접형", "재간접형": "재간접형",
        "부동산": "부동산형", "부동산형": "부동산형",
    }
    if compact_current in aliases:
        product.product.asset_type = aliases[compact_current]
        return

    classification_blob = re.sub(r"\s+", "", " ".join(product.product.classification or []))
    for marker, canonical in (
        ("증권(주식형)", "주식형"), ("주식형", "주식형"),
        ("증권(채권형)", "채권형"), ("채권형", "채권형"),
        ("혼합형", "혼합형"), ("재간접형", "재간접형"), ("부동산형", "부동산형"),
    ):
        if marker in classification_blob:
            product.product.asset_type = canonical
            return

    name = (product.product.name or "").strip()
    explicit_name_match = re.search(r"[\[(]\s*(주식|채권|혼합|재간접|부동산)\s*[\])]", name)
    if explicit_name_match:
        product.product.asset_type = aliases.get(explicit_name_match.group(1))


def _append_narrative_near_duplicate_warning(product: CanonicalProduct) -> None:
    objective = (product.product.investment_objective.text or "").strip()
    strategy = (product.product.investment_strategy.text or "").strip()
    if not objective or not strategy:
        return
    if re.sub(r"\s+", "", objective) == re.sub(r"\s+", "", strategy):
        return
    if not _is_semantic_near_duplicate(objective, strategy):
        return
    warning = "NARRATIVE_NEAR_DUPLICATE: investment_objective and investment_strategy substantially overlap."
    if warning not in product.extraction.warnings:
        product.extraction.warnings.append(warning)


def _is_semantic_near_duplicate(objective: str, strategy: str) -> bool:
    # Conservative deterministic warning gate; it never deletes a value.
    stop = {
        "이", "그", "및", "등", "것을", "합니다", "투자신탁", "투자신탁은",
        "집합투자기구", "집합투자기구는", "투자하여", "투자하고", "투자하는",
    }

    def words(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", text.lower())
            if token not in stop
        }

    left = words(objective)
    right = words(strategy)
    if min(len(left), len(right)) < 6:
        return False
    containment = len(left & right) / min(len(left), len(right))
    strategy_compact = re.sub(r"\s+", "", strategy)
    objective_like_strategy = any(
        marker in strategy_compact
        for marker in ("목적으로합니다", "주목적으로합니다", "수익을추구", "자본이득및배당소득")
    )
    return containment >= 0.72 and objective_like_strategy


def _class_evidence_refs(name: str | None, chunks: list[Chunk] | None) -> list[str]:
    normalized = normalize_class_name(name)
    code = class_code(normalized) or ""
    if not normalized:
        return []
    refs: list[str] = []
    compact_name = re.sub(r"\s+", "", normalized)
    for chunk in chunks or []:
        if chunk.section_type not in {SectionType.FEES, SectionType.PERFORMANCE, SectionType.CLASS_INFO}:
            continue
        hay = re.sub(r"\s+", "", chunk.text or "")
        row_hay = re.sub(r"\s+", "", " ".join(" ".join(row) for row in (chunk.rows or [])))
        if compact_name in hay or compact_name in row_hay:
            refs.append(chunk.chunk_id)
            continue
        if code and re.search(rf"(?:\(|종류){re.escape(code)}(?:\)|(?=[^A-Za-z0-9-]|$))", hay + row_hay, re.I):
            refs.append(chunk.chunk_id)
    return list(dict.fromkeys(refs))

def _keep_extra_class(name: str | None, identity_index: dict[str, int]) -> bool:
    text = normalize_class_name(name)
    if not text:
        return False
    if class_identity(text) in identity_index:
        return True
    if not text.startswith("종류C"):
        return False
    code = class_code(text) or ""
    return "-" in code


def _record_class_outcomes(product: CanonicalProduct) -> None:
    refs = list(
        dict.fromkeys(ref for item in product.classes for ref in item.evidence_refs)
    )
    product.extraction.ownership = [
        item
        for item in product.extraction.ownership
        if not (item.owner == "class_resolver" and item.field == "classes")
    ]
    product.extraction.ownership.append(
        OwnershipOutcome(
            field="classes",
            owner="class_resolver",
            status="VALID" if product.classes else "NOT_FOUND",
            reason=(
                "Canonical class names were resolved from deterministic sources."
                if product.classes
                else "No plausible class names were resolved."
            ),
            evidence_refs=refs,
        )
    )
    product.extraction.candidate_outcomes = [
        item
        for item in product.extraction.candidate_outcomes
        if not (item.owner == "class_resolver" and item.field == "classes")
    ]
    product.extraction.candidate_outcomes.extend(
        CandidateOutcome(
            field="classes",
            owner="class_resolver",
            candidate_id=f"class:{index}:{class_identity(item.class_name)}",
            status="VALID" if item.evidence_refs else "AMBIGUOUS",
            reason=(
                "Class name resolved to a canonical representation with source evidence."
                if item.evidence_refs
                else "Class-like candidate has no supporting source evidence."
            ),
            evidence_refs=list(item.evidence_refs),
        )
        for index, item in enumerate(product.classes)
    )


def recompute_final_warnings(
    product: CanonicalProduct,
    warnings: list[str] | None = None,
) -> list[str]:
    filled = {
        "investment_objective": bool((product.product.investment_objective.text or "").strip()),
        "investment_strategy": bool((product.product.investment_strategy.text or "").strip()),
        "classes": bool(product.classes),
        "fees": bool(product.fees),
        "performance": bool(product.performance),
        "aum": bool(product.aum),
        "investment_risks": bool(product.product.investment_risks),
    }
    stale_sentences = {
        "investment_objective": "투자목적 섹션이 탐지되었으나",
        "investment_strategy": "투자전략 섹션이 탐지되었으나",
        "classes": "클래스 정보가 탐지되었으나",
        "fees": "보수/수수료 표가 탐지되었으나",
        "performance": "수익률 표가 탐지되었으나",
        "aum": "운용규모 섹션이 탐지되었으나",
        "investment_risks": "Investment risk section was detected",
    }
    filled_aliases = {
        alias
        for field, aliases in FIELD_NAME_ALIASES.items()
        if filled.get(field)
        for alias in aliases
    }
    kept: list[str] = []
    source = warnings if warnings is not None else product.extraction.warnings
    final_owner_fields = {item.field for item in product.extraction.ownership if item.status == "VALID"}
    for item in dict.fromkeys(source):
        if "실패:" in item:
            kept.append(item)
            continue
        if _conflicts_with_final_owner(item, final_owner_fields):
            continue
        if _warning_conflicts_with_filled_field(item, filled):
            continue
        field = next((key for key, prefix in stale_sentences.items() if prefix in item), None)
        if field and filled.get(field):
            continue
        if item in filled_aliases:
            continue
        if any(marker in item for marker in STALE_WARNING_MARKERS):
            continue
        if filled["fees"] and _is_empty_table_warning(item, "fee"):
            continue
        if filled["fees"] and (
            "table_gate_ambiguous: fees" in item.lower()
            or ("owner_unresolved" in item.lower() and "fees" in item.lower())
        ):
            continue
        if filled["classes"] and "owner_unresolved" in item.lower() and "classes" in item.lower():
            continue
        if "performance.class_name not in classes" in item.lower():
            class_ids = {class_identity(row.class_name) for row in product.classes}
            performance_ids = {
                class_identity(row.class_name)
                for row in product.performance
                if row.class_name
            }
            if performance_ids.issubset(class_ids):
                continue
        if filled["performance"] and _is_empty_table_warning(item, "performance"):
            continue
        if filled["performance"] and (
            "table_gate_ambiguous: performance" in item.lower()
            or "table_gate_rejected: performance" in item.lower()
            or ("owner_unresolved" in item.lower() and "performance" in item.lower())
        ):
            continue
        low = item.lower()
        if "fee.rate out of typical percent range" in low and not any(
            fee.rate is not None and fee.unit == "%" and not (-1.0 <= fee.rate <= 100.0)
            for fee in product.fees
        ):
            continue
        if filled["investment_objective"] and low.startswith("investment objective") and (
            "truncated" in low or "incomplete" in low
        ):
            continue
        if filled["investment_strategy"] and low.startswith("investment strategy") and (
            "truncated" in low or "incomplete" in low
        ):
            continue
        if (
            filled["investment_objective"]
            and filled["investment_strategy"]
            and "objective" in low
            and "strategy" in low
            and (
                "truncated" in low
                or "incomplete" in low
                or "not fully available" in low
                or "not available" in low
            )
        ):
            continue
        if filled["classes"] and (
            "No explicit class names" in item
            or "No sales class information" in item
            or "No specific sales class information" in item
            or ("classes 배열" in item and ("비어" in item or "비움" in item))
            or ("클래스" in item and "빈 배열" in item)
            or ("판매 클래스" in item and ("찾을 수 없어" in item or "확인되지 않아" in item))
        ):
            continue
        if filled["investment_objective"] and (
            "투자목적" in item and ("null" in item.lower() or "truncation" in item.lower() or "완전하지 않" in item)
        ):
            continue
        if filled["investment_strategy"] and (
            "투자전략" in item and ("null" in item.lower() or "truncation" in item.lower() or "완전하지 않" in item)
        ):
            continue
        kept.append(item)
    return kept


def reconcile_extraction_warnings(
    product: CanonicalProduct,
    warnings: list[str] | None = None,
) -> list[str]:
    return recompute_final_warnings(product, warnings)


def _warning_conflicts_with_filled_field(item: str, filled: dict[str, bool]) -> bool:
    """Drop warnings that are impossible given the final canonical state."""
    compact_item = re.sub(r"\s+", "", item.lower())

    if filled["classes"]:
        markers = (
            "classtablepresentbutclassnamesnotidentifiable", "classnamesnotidentifiable",
            "classinformationnotfound", "noexplicitclassnames", "nosalesclassinformation",
            "nospecificsalesclassinformation", "클래스정보를추출할수없",
            "클래스정보가명시되지않아빈배열", "클래스정보가제공되지않아빈리스트",
            "classes배열이비어", "classes배열을비움", "판매클래스정보를찾을수없",
            "판매클래스정보가확인되지않",
        )
        if any(marker in compact_item for marker in markers):
            return True

    if filled["investment_risks"]:
        markers = (
            "noinvestmentriskinformationfound", "investmentrisksnotfound",
            "investmentrisksectionwasdetectedbutnostructured",
            "투자위험에대한구체적인정보를찾을수없", "투자위험정보를찾을수없",
        )
        if any(marker in compact_item for marker in markers):
            return True

    if filled["investment_objective"]:
        markers = (
            "investmentobjectivetextisincomplete", "investmentobjectivetextistruncated",
            "투자목적텍스트가불완전", "투자목적텍스트가원문에서truncation",
            "투자목적내용이제공되지않아null",
        )
        if any(marker in compact_item for marker in markers):
            return True

    if filled["investment_strategy"]:
        markers = (
            "investmentstrategytextisincomplete", "investmentstrategytextistruncated",
            "투자전략텍스트가불완전", "투자전략텍스트가원문에서truncation",
            "투자전략내용이제공되지않아null",
        )
        if any(marker in compact_item for marker in markers):
            return True

    return False


def _conflicts_with_final_owner(item: str, owner_fields: set[str]) -> bool:
    low = item.lower()
    if "risk_description_missing" in low or "description" in low:
        return False
    stale_terms = (
        "not found",
        "left empty",
        "missing",
        "truncated",
        "incomplete",
        "discarded",
        "mapping unclear",
    )
    if not any(term in low for term in stale_terms):
        return False
    field_tokens = {
        "investment_objective": ("investment objective", "investment_objective", "objective"),
        "investment_strategy": ("investment strategy", "investment_strategy", "strategy"),
        "classes": ("class names", "class information", "classes"),
        "fees": ("fee", "fees"),
        "performance": ("performance",),
        "investment_risks": ("investment risk", "investment_risks"),
    }
    mentioned = {
        field
        for field, tokens in field_tokens.items()
        if any(token in low for token in tokens)
    }
    return bool(mentioned) and mentioned.issubset(owner_fields)


def _is_empty_table_warning(item: str, kind: str) -> bool:
    low = item.lower()
    if "left empty" in low or "mapping unclear" in low:
        if kind == "fee":
            return "fee" in low or "보수" in item or "수수료" in item
        return "performance" in low or "수익률" in item or "실적" in item
    return False
