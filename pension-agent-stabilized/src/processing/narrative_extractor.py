from __future__ import annotations

import re
from dataclasses import dataclass

from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable, ParsedDocument
from processing.risk_row_extractor import collect_table_risk_candidates, compact_risk_text
from parsers.table_parser import is_semantic_risk_table
from processing.risk_record_assembler import RiskRecordAssembler
from processing.risk_region_detector import RiskRegionDetector
from processing.risk_table_selection import select_risks_with_table_policy
from schemas.product import (
    AumItem,
    CandidateOutcome,
    CanonicalProduct,
    InvestmentRiskItem,
    OwnershipOutcome,
    TextWithEvidence,
)

GRADE_LABELS = {
    "매우높은위험",
    "높은위험",
    "다소높은위험",
    "보통위험",
    "낮은위험",
    "매우낮은위험",
    "투자위험",
    "투자위험등급",
    "주요투자위험",
    "투자위험의주요내용",
}
STOP_HEADINGS = (
    "매입방법",
    "환매방법",
    "환매수수료",
    "기준가",
    "과세",
    "전환절차",
    "집합투자업자",
    "투자자유의사항",
    "운용전문",
    "판매회사",
    "참조",
    "모집기간",
    "가.일반위험",
    "가. 일반위험",
    "나.특수위험",
    "집합투자기구의투자위험",
)
GENERIC_RISK_NAMES = {
    "위험",
    "투자위험",
    "주요투자위험",
    "집합투자기구의투자위험",
    "일반위험",
    "특수위험",
    "기타투자위험",
    "기타위험",
    "따른위험",
    "대한위험",
    "관련위험",
    "발생위험",
    "손실위험",
    "변동위험",
    "거래위험",
    "회수위험",
}
POINTER_ONLY = ("참고하시기", "본문의 투자위험", "투자위험 부분")
PURPOSE_PREFIXES = ("이 투자신탁", "이 집합투자기구", "본 투자신탁", "이 펀드")
DISCLAIMER_MARKERS = (
    "반드시실현된다는보장",
    "반드시실현된다는보장이",
    "성과목표는반드시",
    "성과목표가반드시",
    "과거의운용실적",
    "미래수익을보장",
)
GARBAGE_EXACT = {
    "및",
    "투자전략에따른",
    "투자목적",
    "투자전략",
    "투자목적및투자전략",
    "투자목적또는성과목표",
}
NARRATIVE_STOP_RE = re.compile(
    r"(?:분\s*류)|"
    r"(?:\n\s*|\s{2,}|(?<=\.)\s+)(?:"
    r"분류\s|상품종류|주요\s*투자위험|매입방법|환매방법|"
    r"판매수수료|집합투자기구의\s*투자대상"
    r")"
)
HEADING_RE = re.compile(
    r"^(?:\d+\.\s*)?(?:투자목적\s*및\s*투자전략|투자목적및투자전략|투자목적|투자전략)\s*[:：]?",
)
SUBHEAD_RE = re.compile(
    r"^(?:및\s*위험관리\s*)(?:\(\d+\)\s*)?(?:투자전략|운용전략)?(?:\s*및\s*투자방침)?(?:\s*\([^)]+\))?\s*"
    r"|^(?:\(\d+\)\s+)투자전략(?:\s*\([^)]+\))?\s*"
)
COMPLETE_END_RE = re.compile(r"(?:다|입니다|합니다|함)\.\s*$")
STRATEGY_BOILERPLATE = (
    "자산운용보고서",
    "수익자에게 교부",
    "신탁업자의 확인",
    "투자설명서의 내용",
)
STRATEGY_DISCLAIMER_MARKERS = (
    "반드시귀결되는것은아닙니다",
    "성과로반드시귀결",
    "성과를보장하지",
    "수익을보장하지",
    "투자성과를보장",
    "실현된다는보장",
)
NUMBERED_OBJECTIVE_RE = re.compile(
    r"집합투자기구의\s*투자목적\s+(이\s+(?:투자신탁|집합투자기구).{20,700}?)(?=\n\d+\.|\n가\.|8\.\s*집합투자기구의 투자대상)",
    re.S,
)
NUMBERED_STRATEGY_RE = re.compile(
    r"집합투자기구의\s*투자전략[^\n]{0,40}\s+(이\s+(?:투자신탁|집합투자기구).{20,800}?)(?=\n\d+\.|\n가\.|위험관리)",
    re.S,
)
NARRATIVE_SECTIONS = {
    SectionType.INVESTMENT_OBJECTIVE,
    SectionType.INVESTMENT_STRATEGY,
}


@dataclass
class NarrativeCandidate:
    text: str
    chunk_id: str
    role: str
    score: int
    section: SectionType | None = None
    combined: bool = False
    refs: list[str] | None = None


def is_garbage_narrative(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw:
        return True
    blob = re.sub(r"\s+", "", raw)
    if blob in GARBAGE_EXACT:
        return True
    if len(blob) < 18:
        return True
    if blob.endswith("과거의") or raw.endswith("과거의"):
        return True
    if blob.endswith("따른") and len(blob) < 30:
        return True
    if any(marker in blob for marker in DISCLAIMER_MARKERS):
        return True
    if raw.startswith("투자목적 또는") or raw.startswith("투자목적또는"):
        return True
    if raw.startswith("또한 과거의") or raw.startswith("또한과거의"):
        return True
    return False


def is_complete_narrative(text: str | None, role: str | None = None) -> bool:
    if is_garbage_narrative(text):
        return False
    raw = _clean_prose(text or "")
    if not _ends_complete(raw) and not (
        role == "objective" and _is_objective_label_value(raw)
    ):
        return False
    if len(raw) < 24:
        return False
    if role == "strategy":
        if is_strategy_disclaimer(raw) or is_strategy_contaminated(raw):
            return False
        return _looks_like_purpose(raw) or _strategy_score(raw) >= 20
    return _looks_like_purpose(raw)


def is_strategy_disclaimer(text: str | None) -> bool:
    blob = _compact(text)
    if not blob:
        return False
    if any(marker in blob for marker in STRATEGY_DISCLAIMER_MARKERS):
        return True
    # 면책/성과 비보장 문장은 실제 운용행위(투자/편입/운용)를 설명하지 않으면 전략이 아니다.
    disclaimer = any(token in blob for token in ("보장", "귀결", "성과목표", "과거의운용실적"))
    action = any(token in blob for token in ("투자합니다", "투자하며", "투자하고", "편입", "운용합니다", "운용하고", "추종"))
    return disclaimer and not action


def is_strategy_contaminated(text: str | None) -> bool:
    """Reject document navigation/change history masquerading as strategy."""
    raw = _clean_prose(text or "")
    compact = _compact(raw)
    if not compact:
        return False

    toc_markers = sum(marker in compact for marker in (
        "집합투자기구의투자위험",
        "매입환매전환절차",
        "기준가격산정기준",
        "보수및수수료에관한사항",
        "집합투자기구의재무및운용실적",
        "집합투자기구관련회사에관한사항",
        "기타투자자보호를위해필요한사항",
        "상세목차",
    ))
    part_markers = len(re.findall(r"제\s*[2-5]\s*부", raw))
    numbered_headings = len(re.findall(r"(?:^|\s)(?:1[0-4]|[1-9])\.\s*[^.]{2,45}", raw))
    if toc_markers >= 2 and (part_markers >= 1 or numbered_headings >= 3):
        return True

    dates = len(re.findall(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}", raw))
    change_markers = sum(marker in compact for marker in (
        "개정사항반영", "운용실적갱신", "재무제표갱신", "서식작성기준",
        "투자위험추가", "업데이트", "효력발생일",
    ))
    if dates >= 2 and change_markers >= 2:
        return True

    return False


def is_semantic_risk_description(name: str | None, description: str | None) -> bool:
    n = _compact(name)
    d = _compact(description)
    if not n or not d:
        return False
    # 위험등급 표의 '수익률 변동성(표준편차)' 설명은 위험항목 자체가 아니다.
    if "수익률변동성" in n and ("표준편차" in d or "보여주는수치" in d):
        return False
    # 한 risk description 안에 다음 독립 위험 제목이 붙은 경우는 미분리 상태로 간주한다.
    if re.search(r"(?:^|[.!?]\s*)(?:세금관련\s*위험|과세\s*위험|해지의\s*위험)\s+", description or "") and not any(token in n for token in ("세금", "과세")):
        return False
    loss_mechanism = any(token in d for token in (
        "손실", "하락", "변동", "부도", "채무불이행", "회수", "유동성", "환매",
        "해지", "파산", "신용", "위험", "불가능", "감소", "괴리", "노출"
    ))
    return loss_mechanism


def is_investment_risk_candidate(
    name: str | None,
    description: str | None = None,
    context_text: str | None = None,
) -> bool:
    """High precision role gate for investment risk names.

    A phrase may contain '위험' but still be a performance metric definition,
    footnote, disclaimer, or manager/performance note. Those are rejected here
    before they can survive as final investment_risks.
    """
    n = _compact(name)
    d = _compact(description)
    c = _compact(context_text)
    if not n:
        return False
    if n in GENERIC_RISK_NAMES:
        return False
    if n.endswith("위험등") and any(
        mark in (name or "") for mark in (",", "，", "/", ";")
    ):
        return False

    metric_name = any(token in n for token in (
        "수익률변동성", "표준편차", "연평균수익률", "평균수익률",
        "변동성표준편차",
    ))
    metric_context = any(token in c for token in (
        "보여주는수치", "연환산주간수익률", "평균수익률에서",
        "연평균수익률은", "기하평균", "운용현황", "동종집합투자기구",
    ))
    if metric_name and (metric_context or not d):
        return False

    # 표/성과 주석 문맥에서만 등장하는 '위험' 표현은 투자위험 항목이 아니다.
    if any(token in c for token in ("비교지수", "연평균수익률", "운용전문인력")) and metric_name:
        return False

    # 실제 위험은 명칭 또는 설명에서 손실 메커니즘을 가져야 한다.
    risk_name_signal = any(token in n for token in (
        "원본손실", "원금손실", "가격변동", "금리", "이자율", "신용",
        "부도", "유동성", "환매", "해지", "파산", "환율", "집중",
        "차입", "공매도", "거래상대방", "레버리지", "괴리", "세금",
        "과세", "종목위험",
    ))
    if risk_name_signal:
        return True
    if d and is_semantic_risk_description(name, description):
        return True
    return False


def _ends_complete(text: str | None) -> bool:
    return bool(COMPLETE_END_RE.search((text or "").strip()))


def apply_narrative_facts(
    product: CanonicalProduct,
    chunks: list[Chunk] | None = None,
    tables: list[DetectedTable] | None = None,
    parsed: ParsedDocument | None = None,
) -> CanonicalProduct:
    chunks = chunks or []
    selected = _select_narratives(
        chunks,
        avoid_texts=[product.product.investment_objective.text],
    )
    _assign_narrative(product, "investment_objective", selected.get("objective"))
    _assign_narrative(product, "investment_strategy", selected.get("strategy"))
    _separate_objective_and_strategy(product, selected)

    # A reconstructed table row has stronger geometry and provenance than a
    # free-form page block, so it is always the primary deterministic source.
    extracted_risks = _extract_risks(chunks, tables)
    if not extracted_risks and parsed is not None:
        regions = RiskRegionDetector().detect(parsed, chunks)
        records, diagnostics = RiskRecordAssembler().assemble(
            regions, tables or parsed.tables, chunks
        )
        product.extraction.risk_diagnostics = diagnostics
        viable_pages = [
            record.name_span.page_number
            for record in records
            if is_investment_risk_candidate(
                record.name, record.description, record.description
            )
            and _normalize_risk(
                record.name, record.description, list(record.evidence_refs)
            )
        ]
        allowed_pages = _best_risk_page_window(viable_pages)
        for record in records:
            if allowed_pages and record.name_span.page_number not in allowed_pages:
                continue
            if not is_investment_risk_candidate(
                record.name, record.description, record.description
            ):
                continue
            item = _normalize_risk(
                record.name, record.description, list(record.evidence_refs)
            )
            if item:
                extracted_risks.append(item)
        extracted_risks = _dedupe_risks(extracted_risks)
    # HIGH/MEDIUM confidence risk tables are primary; LLM preserve must not
    # block deterministic table promotion. LOW keeps legacy preserve/fallback.
    selected_risks, _assessment = select_risks_with_table_policy(
        existing=list(product.product.investment_risks),
        extracted=extracted_risks,
        chunks=chunks,
        tables=tables,
        normalize=_normalize_risk,
        should_preserve_existing=_should_preserve_existing_risks,
    )
    product.product.investment_risks = _sanitize_risk_evidence(selected_risks, chunks)
    _record_narrative_outcomes(product)
    _record_risk_outcomes(product)
    if not product.aum:
        product.aum = _extract_aum(chunks)
    return product


def _assign_narrative(
    product: CanonicalProduct,
    field: str,
    picked: NarrativeCandidate | None,
) -> None:
    current = getattr(product.product, field)
    current_text = (current.text or "").strip()
    role = "strategy" if "strategy" in field else "objective"
    cleaned_current = _trim_selected(current_text, role)
    if cleaned_current != current_text and is_complete_narrative(cleaned_current, role):
        current_text = cleaned_current
        setattr(
            product.product,
            field,
            TextWithEvidence(text=current_text, evidence_refs=list(current.evidence_refs)),
        )
    cleared_duplicate = False
    if field == "investment_strategy":
        objective = (product.product.investment_objective.text or "").strip()
        if current_text and _compact(current_text) == _compact(objective):
            _record_narrative_rejection(
                product,
                field,
                list(current.evidence_refs),
                "Same normalized text as investment_objective.",
            )
            current_text = ""
            setattr(product.product, field, TextWithEvidence())
            cleared_duplicate = True
    if is_complete_narrative(current_text, role):
        return
    if picked and is_complete_narrative(picked.text, picked.role):
        text = _trim_selected(picked.text, picked.role)
        if not is_complete_narrative(text, picked.role):
            text = picked.text
        refs = [item for item in (picked.refs or [picked.chunk_id]) if item]
        setattr(
            product.product,
            field,
            TextWithEvidence(text=text, evidence_refs=refs),
        )
        return
    if cleared_duplicate:
        warning = "investment_strategy discarded: identical to investment_objective"
        if warning not in product.extraction.warnings:
            product.extraction.warnings.append(warning)
        return
    if current_text:
        setattr(product.product, field, TextWithEvidence())
        warning = f"{field} discarded: disclaimer/fragment"
        if warning not in product.extraction.warnings:
            product.extraction.warnings.append(warning)


def _separate_objective_and_strategy(
    product: CanonicalProduct,
    selected: dict[str, NarrativeCandidate],
) -> None:
    objective = (product.product.investment_objective.text or "").strip()
    strategy = (product.product.investment_strategy.text or "").strip()
    if not objective or not strategy:
        return
    if _compact(objective) != _compact(strategy):
        return
    alt = selected.get("strategy")
    if (
        alt
        and _compact(alt.text) != _compact(objective)
        and is_complete_narrative(alt.text, "strategy")
    ):
        text = _trim_selected(alt.text, "strategy")
        if not is_complete_narrative(text, "strategy"):
            text = alt.text
        if _compact(text) == _compact(objective):
            text = ""
        if not text:
            _record_narrative_rejection(
                product,
                "investment_strategy",
                list(alt.refs or [alt.chunk_id]),
                "Same normalized text as investment_objective after cleanup.",
            )
            product.product.investment_strategy = TextWithEvidence()
            warning = "investment_strategy discarded: identical to investment_objective"
            if warning not in product.extraction.warnings:
                product.extraction.warnings.append(warning)
            return
        refs = [item for item in (alt.refs or [alt.chunk_id]) if item]
        product.product.investment_strategy = TextWithEvidence(text=text, evidence_refs=refs)
        return
    _record_narrative_rejection(
        product,
        "investment_strategy",
        list(product.product.investment_strategy.evidence_refs),
        "Same normalized text as investment_objective.",
    )
    product.product.investment_strategy = TextWithEvidence()
    warning = "investment_strategy discarded: identical to investment_objective"
    if warning not in product.extraction.warnings:
        product.extraction.warnings.append(warning)


def _select_narratives(
    chunks: list[Chunk],
    avoid_texts: list[str | None] | None = None,
) -> dict[str, NarrativeCandidate]:
    objective_pool = _collect_candidates(chunks, "objective")
    strategy_pool = [
        item for item in _collect_candidates(chunks, "strategy")
        if item.section in {
            SectionType.INVESTMENT_OBJECTIVE,
            SectionType.INVESTMENT_STRATEGY,
            SectionType.OTHER,
            SectionType.PERFORMANCE,
        }
    ]
    objective = _best_candidate(
        [item for item in objective_pool if item.section == SectionType.INVESTMENT_OBJECTIVE]
        or [item for item in objective_pool if item.combined]
        # Section detection is a ranking signal for an explicitly labelled
        # objective row, but ordinary strategy prose must not be promoted.
        or [
            item for item in objective_pool
            if item.section != SectionType.INVESTMENT_STRATEGY
            or _is_objective_label_value(item.text)
        ]
    )
    avoided = {
        _compact(text)
        for text in [objective.text if objective else "", *(avoid_texts or [])]
        if text
    }
    strategy = _best_candidate(
        [item for item in strategy_pool if _narrative_identity(item.text, "strategy") not in avoided],
        prefer_strategy=True,
    )
    if strategy is None:
        strategy = _best_candidate(
            [
                item for item in _strategy_table_candidates(chunks)
                if _narrative_identity(item.text, "strategy") not in avoided
            ],
            prefer_strategy=True,
        )
    if objective and strategy and _compact(objective.text) == _compact(strategy.text):
        strategy = None
    picked: dict[str, NarrativeCandidate] = {}
    if objective:
        picked["objective"] = objective
    if strategy:
        picked["strategy"] = strategy
    return picked


def _narrative_identity(text: str | None, role: str) -> str:
    normalized = _trim_selected(text or "", role)
    normalized = re.sub(r"^\s*\d+\)\s*", "", normalized)
    return _compact(normalized)


def _strategy_table_candidates(chunks: list[Chunk]) -> list[NarrativeCandidate]:
    """Recover prose trapped inside a reconstructed table cell."""
    found: list[NarrativeCandidate] = []
    for chunk in chunks:
        if not chunk.table_id or not chunk.rows:
            continue
        for row in chunk.rows:
            for cell in row:
                compact = _compact(cell)
                if "투자전략" not in compact or not any(token in compact for token in (
                    "이상투자", "주로투자", "포트폴리오를구성", "투자비중을조정",
                )):
                    continue
                proxy = chunk.model_copy(update={
                    "text": cell,
                    "table_id": None,
                    "section_type": SectionType.INVESTMENT_STRATEGY,
                })
                for sentence in _candidate_sentences(cell, "strategy"):
                    score = _score_candidate(sentence, proxy, "strategy", cell)
                    if score <= 0:
                        continue
                    found.append(NarrativeCandidate(
                        text=_clip_sentence(sentence),
                        chunk_id=chunk.chunk_id,
                        role="strategy",
                        score=score,
                        section=SectionType.INVESTMENT_STRATEGY,
                        refs=[chunk.chunk_id],
                    ))
    found.sort(key=lambda item: item.score, reverse=True)
    return found[:8]


def _collect_candidates(chunks: list[Chunk], role: str) -> list[NarrativeCandidate]:
    found: list[NarrativeCandidate] = []
    for index, chunk in enumerate(chunks):
        if chunk.table_id:
            continue
        window, refs = _window_text(chunks, index)
        if role == "objective":
            labelled = _objective_label_value(window)
            if labelled:
                found.append(
                    NarrativeCandidate(
                        text=labelled,
                        chunk_id=refs[0],
                        role=role,
                        score=260,
                        section=chunk.section_type,
                        combined=_combined_heading(window),
                        refs=list(refs),
                    )
                )
        for sentence in _candidate_sentences(window, role):
            score = _score_candidate(sentence, chunk, role, window)
            if score <= 0:
                continue
            found.append(
                NarrativeCandidate(
                    text=_clip_sentence(sentence),
                    chunk_id=refs[0],
                    role=role,
                    score=score,
                    section=chunk.section_type,
                    combined=_combined_heading(window),
                    refs=list(refs),
                )
            )
        numbered = NUMBERED_OBJECTIVE_RE if role == "objective" else NUMBERED_STRATEGY_RE
        match = numbered.search(chunk.text or "")
        if match:
            prose = _clip_sentence(_clean_prose(match.group(1)))
            if is_complete_narrative(prose, role):
                found.append(
                    NarrativeCandidate(
                        text=prose,
                        chunk_id=chunk.chunk_id,
                        role=role,
                        score=220,
                        section=chunk.section_type,
                        combined=_combined_heading(chunk.text or ""),
                        refs=[chunk.chunk_id],
                    )
                )
    found.sort(key=lambda item: item.score, reverse=True)
    return found[:8]


def _objective_label_value(text: str | None) -> str | None:
    """Read the value directly below an explicit 투자목적 label."""
    match = re.search(
        r"(?:^|\n)\s*투자목적\s*(?:\n|[:：])\s*"
        r"([-–—ㆍ·▪•▶▷●○□◇※*]?\s*.*?)(?="
        r"\n\s*(?:주요\s*투자전략|투자전략|비교지수|분류|투자비용|\d+\.\s)|\Z)",
        text or "",
        re.S,
    )
    if not match:
        return None
    value = _clean_prose(match.group(1)).strip(" -–—ㆍ·▪•")
    return value if _is_objective_label_value(value) else None


def _best_candidate(
    pool: list[NarrativeCandidate],
    prefer_strategy: bool = False,
) -> NarrativeCandidate | None:
    complete = [item for item in pool if is_complete_narrative(item.text, item.role)]
    if not complete:
        return None
    if prefer_strategy:
        complete = sorted(
            complete,
            key=lambda item: (_strategy_score(item.text), item.score),
            reverse=True,
        )
    return complete[0]


def recover_objective_from_chunks(chunks: list[Chunk]) -> TextWithEvidence | None:
    """Deterministically recover one source-grounded objective sentence.

    Recovery is intentionally sentence-scoped: a disclaimer in the same chunk
    cannot invalidate an otherwise valid objective sentence.
    """
    objective_context = sorted([
        chunk for chunk in chunks
        if not chunk.table_id
        and chunk.section_type in {
            SectionType.INVESTMENT_OBJECTIVE,
            SectionType.INVESTMENT_STRATEGY,
            SectionType.OTHER,
        }
    ], key=lambda chunk: (chunk.page_start, chunk.chunk_id))
    candidate = _best_candidate(_collect_candidates(objective_context, "objective"))
    if candidate is None:
        return None
    refs = list(dict.fromkeys(candidate.refs or [candidate.chunk_id]))
    return TextWithEvidence(text=candidate.text, evidence_refs=refs)


def recover_strategy_from_chunks(chunks: list[Chunk]) -> TextWithEvidence | None:
    """Deterministically select one complete, source-grounded strategy."""
    context = sorted([
        chunk for chunk in chunks
        if not chunk.table_id
        and chunk.section_type in {
            SectionType.INVESTMENT_OBJECTIVE,
            SectionType.INVESTMENT_STRATEGY,
            SectionType.OTHER,
        }
    ], key=lambda chunk: (chunk.page_start, chunk.chunk_id))
    selected = _select_narratives(context).get("strategy")
    if selected is None:
        # Layout classifiers occasionally label a strategy paragraph as
        # PERFORMANCE when the following sentence introduces its benchmark.
        # Only consult these source paragraphs as a deterministic fallback.
        fallback = sorted([
            chunk for chunk in chunks
            if not chunk.table_id
            and chunk.section_type == SectionType.PERFORMANCE
            and any(token in _compact(chunk.text) for token in (
                "이투자신탁은", "모투자신탁에", "이상투자", "포트폴리오를구성",
            ))
        ], key=lambda chunk: (chunk.page_start, chunk.chunk_id))
        selected = _select_narratives(fallback).get("strategy")
    if selected is None or not is_complete_narrative(selected.text, "strategy"):
        return None
    text = _trim_selected(selected.text, "strategy")
    if not is_complete_narrative(text, "strategy"):
        text = selected.text
    refs = list(dict.fromkeys(selected.refs or [selected.chunk_id]))
    return TextWithEvidence(text=text, evidence_refs=refs)


def _window_text(chunks: list[Chunk], index: int) -> tuple[str, list[str]]:
    chunk = chunks[index]
    text = chunk.text or ""
    refs = [chunk.chunk_id]
    for nxt in chunks[index + 1 : index + 3]:
        if not _can_merge_narrative(chunk, nxt):
            break
        text = f"{text}\n{nxt.text or ''}"
        refs.append(nxt.chunk_id)
        last = _last_sentence(text)
        if _ends_complete(last) or is_garbage_narrative(last):
            break
    return text, refs


def _can_merge_narrative(chunk: Chunk, nxt: Chunk) -> bool:
    if nxt.table_id:
        return False
    if nxt.page_start < chunk.page_start or nxt.page_start > chunk.page_end + 1:
        return False
    if nxt.section_type not in {chunk.section_type, SectionType.OTHER, *NARRATIVE_SECTIONS}:
        return False
    if nxt.section_type in {
        SectionType.FEES,
        SectionType.PERFORMANCE,
        SectionType.INVESTMENT_RISK,
        SectionType.CLASS_INFO,
    }:
        return False
    return not _starts_stop_heading(nxt.text or "")


def _last_sentence(text: str) -> str:
    parts = _split_sentences(_cut_next_section(_strip_heading(text)))
    return parts[-1] if parts else ""


def _split_sentences(text: str) -> list[str]:
    prose = _clean_prose(text)
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*", prose)
        if part.strip()
    ]


def _glue_incomplete_parts(parts: list[str]) -> list[str]:
    if not parts:
        return []
    out = [parts[0]]
    for part in parts[1:]:
        prev = out[-1]
        if not _ends_complete(prev):
            out[-1] = f"{prev} {part}".strip()
        else:
            out.append(part)
    return out


def _candidate_sentences(text: str, role: str | None = None) -> list[str]:
    body = _cut_next_section(_strip_heading(text))
    parts = _split_sentences(body)
    if role == "strategy":
        parts = _glue_incomplete_parts(parts)
    return [part for part in parts if not is_garbage_narrative(part)]


def _score_candidate(sentence: str, chunk: Chunk, role: str, window: str) -> int:
    if is_garbage_narrative(sentence):
        return 0
    if any(token in sentence for token in STRATEGY_BOILERPLATE):
        return 0
    if role == "strategy":
        if is_strategy_contaminated(sentence):
            return 0
        if not (_looks_like_purpose(sentence) or _strategy_score(sentence) >= 20):
            return 0
        compact_sentence = _compact(sentence)
        # Summary/table rows that bundle objective, benchmark, or a master-fund
        # catalogue are context, not the canonical strategy sentence.
        if "투자목적" in compact_sentence and "비교지수" in compact_sentence:
            return 0
        if "주된투자대상" in compact_sentence and "비교지수" in compact_sentence:
            return 0
        if "모투자신탁명칭주요투자전략" in compact_sentence:
            return 0
    elif not _looks_like_purpose(sentence):
        return 0
    score = 40
    if is_complete_narrative(sentence, role):
        score += 40
    if role == "objective" and chunk.section_type == SectionType.INVESTMENT_OBJECTIVE:
        score += 100
    if role == "strategy" and chunk.section_type == SectionType.INVESTMENT_STRATEGY:
        score += 100
    if role == "objective" and chunk.section_type == SectionType.INVESTMENT_STRATEGY:
        score += 20 if _combined_heading(window) else -20
    if role == "strategy" and chunk.section_type == SectionType.INVESTMENT_OBJECTIVE:
        score += 20 if _combined_heading(window) else -20
    head = _strip_heading(window)
    if _clean_prose(head).startswith(sentence[:24]):
        score += 80
    if any(marker in _compact(window[:180]) for marker in DISCLAIMER_MARKERS):
        score -= 30
    if chunk.page_start <= 4 and any(marker in _compact(window) for marker in DISCLAIMER_MARKERS):
        score -= 20
    if role == "objective":
        score += 25 if any(token in sentence for token in ("목적으로", "수익을 추구", "추구하는 것을 목적")) else 0
    if role == "strategy":
        score += _strategy_score(sentence)
    length = len(sentence)
    if 60 <= length <= 420:
        score += 15
    elif length > 700:
        score -= 20
    return score


def _strategy_score(text: str) -> int:
    blob = text or ""
    if any(token in blob for token in STRATEGY_BOILERPLATE):
        return 0
    score = 0
    for token in (
        "운용",
        "자산총액",
        "이상 투자",
        "이상을 투자",
        "투자합니다",
        "투자하고자",
        "추종",
        "종목별",
        "투자전략",
        "모투자신탁에 투자",
    ):
        if token in blob:
            score += 20
    return score


def _trim_selected(text: str, role: str) -> str:
    cleaned = _clean_leading_structure(text, role)
    cleaned = re.sub(r"^\([^)]*(?:운용전략|투자방침)[^)]*\)\s*", "", cleaned)
    cleaned = re.sub(r"^투자전략\s*[①①1]?\s*", "", cleaned)
    start = re.search(r"(이\s+(?:투자신탁|집합투자기구)|본\s+투자신탁)", cleaned)
    prefix = cleaned[: start.start()].strip() if start else ""
    if start and start.start() <= 80 and prefix not in {"①", "②", "③", "•"}:
        cleaned = cleaned[start.start() :]
    return _clip_sentence(cleaned)


def _clean_leading_structure(text: str | None, role: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^[,;:\-–—\s]+", "", cleaned)
    cleaned = re.sub(r"^\d{1,3}\s+(?=[가-힣A-Za-z])", "", cleaned)
    if role == "strategy":
        marker = re.search(
            r"(?:(?:가\.\s*)?투자전략\s*및\s*위험관리\s*"
            r"(?:\(\d+\)\s*)?투자전략|"
            r"\(\d+\)\s*투자전략)\s*"
            r"(?:\([^)]*(?:운용전략|투자방침)[^)]*\))?\s*",
            cleaned[:180],
        )
        if marker:
            cleaned = cleaned[marker.end() :].lstrip(" ,:：")
        else:
            cleaned = re.sub(
                r"^(?:위험관리\s*및\s*수익구조\s*)"
                r"(?:가\.\s*)?(?:투자전략\s*및\s*위험관리\s*)?",
                "",
                cleaned,
            ).lstrip(" ,:：")
    return cleaned


def _clip_sentence(text: str, limit: int = 700) -> str:
    compact = _cut_next_section(_clean_prose(text))
    if _ends_complete(compact) and len(compact) <= 800:
        return compact
    if len(compact) > limit:
        cut = compact[:limit]
        period = cut.rfind("다.")
        compact = cut[: period + 2] if period >= 80 else cut.rstrip()
    if not _ends_complete(compact) and "다." in compact:
        compact = compact[: compact.rfind("다.") + 2]
    return compact


def _cut_next_section(text: str) -> str:
    match = NARRATIVE_STOP_RE.search(text or "")
    if match and match.start() > 24:
        return (text or "")[: match.start()].rstrip()
    return text or ""


def _starts_stop_heading(text: str) -> bool:
    head = _compact((text or "")[:24])
    return any(head.startswith(_compact(token)) for token in ("분류", "상품종류", "주요투자위험", "매입방법", "환매방법", "판매수수료"))


def _combined_heading(text: str) -> bool:
    head = _compact(text)[:80]
    return "투자목적및투자전략" in head or ("투자목적" in head and "투자전략" in head)


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def _extract_risks(
    chunks: list[Chunk],
    tables: list[DetectedTable] | None = None,
) -> list[InvestmentRiskItem]:
    found: list[InvestmentRiskItem] = []
    seen: set[str] = set()
    chunk_map = {chunk.chunk_id: chunk.text or "" for chunk in chunks}

    def add(name: str | None, description: str | None, refs: list[str]) -> None:
        context = " ".join(chunk_map.get(ref, "") for ref in refs)
        if not is_investment_risk_candidate(name, description, context):
            return
        item = _normalize_risk(name, description, refs)
        if not item:
            return
        # A normalized name may only remove layout whitespace/punctuation; it
        # must remain a literal substring of the bound source evidence.
        if context and compact_risk_text(item.name) not in compact_risk_text(context):
            return
        key = re.sub(r"\s+", "", item.name or "")
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    risk_tables = [table for table in tables or [] if _is_risk_table(table)]
    table_page = {table.table_id: table.page_number for table in risk_tables}
    table_candidates = collect_table_risk_candidates(chunks, risk_tables)
    viable_pages = []
    for candidate in table_candidates:
        context = " ".join(
            chunk_map.get(ref, "") for ref in candidate.evidence_refs
        )
        if (
            candidate.table_id in table_page
            and is_investment_risk_candidate(
                candidate.name, candidate.description, context
            )
            and _normalize_risk(
                candidate.name,
                candidate.description,
                list(candidate.evidence_refs),
            )
        ):
            viable_pages.append(table_page[candidate.table_id])
    allowed_pages = _best_risk_page_window(viable_pages)
    for candidate in table_candidates:
        table = next(
            (item for item in risk_tables if item.table_id == candidate.table_id),
            None,
        )
        if table is None or (allowed_pages and table.page_number not in allowed_pages):
            continue
        add(
            candidate.name,
            candidate.description,
            list(candidate.evidence_refs),
        )
    risk_chunks = [
        chunk
        for chunk in chunks
        if not chunk.table_id and _risk_section(chunk, chunk.text or "")
    ]
    if risk_chunks:
        earliest = min(chunk.page_start for chunk in risk_chunks)
        risk_chunks = [chunk for chunk in risk_chunks if chunk.page_start <= earliest + 1]
    for chunk in risk_chunks:
        for name, desc in _split_risk_blocks(chunk.text or ""):
            add(name, desc, [chunk.chunk_id])
    return _dedupe_risks(found)


def _best_risk_page_window(viable_pages: list[int]) -> set[int] | None:
    """Choose the richest adjacent-page risk table, preferring the earliest tie.

    Prospectuses commonly repeat a compact risk table in the summary and a detailed
    table later. A malformed one-row summary must not hide a complete main table,
    while an equally complete summary should remain authoritative.
    """
    if not viable_pages:
        return None
    candidates = sorted(set(viable_pages))
    start = min(
        candidates,
        key=lambda page: (
            -sum(candidate in {page, page + 1} for candidate in viable_pages),
            page,
        ),
    )
    return {start, start + 1}


def _should_preserve_existing_risks(risks: list[InvestmentRiskItem]) -> bool:
    """Keep an already-assembled risk list instead of reselection.

    Baseline documents typically have several named risks with usable
    descriptions. A single truncated placeholder must remain replaceable.
    """
    if len(risks) < 3:
        return False
    with_desc = sum(
        1
        for risk in risks
        if (risk.name or "").strip()
        and len((risk.description or "").strip()) >= 40
    )
    return with_desc >= max(3, (len(risks) * 3 + 4) // 5)


def _sanitize_risk_evidence(
    items: list[InvestmentRiskItem],
    chunks: list[Chunk],
) -> list[InvestmentRiskItem]:
    chunk_map = {chunk.chunk_id: chunk.text or "" for chunk in chunks}
    sanitized: list[InvestmentRiskItem] = []
    for item in items:
        description = (item.description or "").strip()
        evidence = " ".join(chunk_map.get(ref, "") for ref in item.evidence_refs)
        supported = bool(
            description
            and evidence
            and (
                _compact(description) in _compact(evidence)
                or _risk_evidence_overlap(description, evidence) >= 0.65
            )
            and _ends_complete(description)
            and is_semantic_risk_description(item.name, description)
        )
        sanitized.append(
            item.model_copy(update={"description": description if supported else None})
        )
    return sanitized


def _risk_evidence_overlap(description: str, evidence: str) -> float:
    tokens = {
        token
        for token in re.findall(r"[가-힣A-Za-z0-9]+", description)
        if len(token) >= 2
    }
    if not tokens:
        return 0.0
    source = set(re.findall(r"[가-힣A-Za-z0-9]+", evidence))
    return len(tokens & source) / len(tokens)


def _record_narrative_outcomes(product: CanonicalProduct) -> None:
    fields = {
        "investment_objective": product.product.investment_objective,
        "investment_strategy": product.product.investment_strategy,
    }
    retained = [
        item
        for item in product.extraction.ownership
        if not (item.owner == "narrative" and item.field in fields)
    ]
    for field, value in fields.items():
        role = "strategy" if field.endswith("strategy") else "objective"
        text = (value.text or "").strip()
        retained.append(
            OwnershipOutcome(
                field=field,
                owner="narrative",
                status="VALID" if is_complete_narrative(text, role) else (
                    "REJECTED" if text else "NOT_FOUND"
                ),
                reason=(
                    "Narrative passed cleanup and completeness checks."
                    if is_complete_narrative(text, role)
                    else "Narrative missing or incomplete."
                ),
                evidence_refs=list(value.evidence_refs),
            )
        )
        product.extraction.candidate_outcomes = [
            item
            for item in product.extraction.candidate_outcomes
            if not (
                item.owner == "narrative"
                and item.field == field
                and item.candidate_id.endswith(":selected")
            )
        ]
        if is_complete_narrative(text, role):
            product.extraction.candidate_outcomes.append(
                CandidateOutcome(
                    field=field,
                    owner="narrative",
                    candidate_id=f"narrative:{field}:selected",
                    status="VALID",
                    reason="Selected narrative passed cleanup and completeness checks.",
                    evidence_refs=list(value.evidence_refs),
                )
            )
    product.extraction.ownership = retained


def _record_narrative_rejection(
    product: CanonicalProduct,
    field: str,
    evidence_refs: list[str],
    reason: str,
) -> None:
    candidate_id = f"narrative:{field}:duplicate"
    product.extraction.candidate_outcomes = [
        item
        for item in product.extraction.candidate_outcomes
        if not (
            item.owner == "narrative"
            and item.field == field
            and item.candidate_id == candidate_id
        )
    ]
    product.extraction.candidate_outcomes.append(
        CandidateOutcome(
            field=field,
            owner="narrative",
            candidate_id=candidate_id,
            status="REJECTED",
            reason=reason,
            evidence_refs=list(dict.fromkeys(evidence_refs)),
        )
    )


def _record_risk_outcomes(product: CanonicalProduct) -> None:
    risks = product.product.investment_risks
    refs = list(
        dict.fromkeys(ref for risk in risks for ref in risk.evidence_refs)
    )
    product.extraction.ownership = [
        item
        for item in product.extraction.ownership
        if not (item.owner == "narrative" and item.field == "investment_risks")
    ]
    product.extraction.ownership.append(
        OwnershipOutcome(
            field="investment_risks",
            owner="narrative",
            status="VALID" if risks else "NOT_FOUND",
            reason=(
                "Risk names were retained with source evidence."
                if risks
                else "No supported investment risk items were found."
            ),
            evidence_refs=refs,
        )
    )
    product.extraction.candidate_outcomes = [
        item
        for item in product.extraction.candidate_outcomes
        if not (item.owner == "narrative" and item.field == "investment_risks")
    ]
    for index, risk in enumerate(risks):
        has_description = bool((risk.description or "").strip())
        product.extraction.candidate_outcomes.append(
            CandidateOutcome(
                field="investment_risks",
                owner="narrative",
                candidate_id=f"risk:{index}:{_compact(risk.name)}",
                status="VALID" if has_description else "AMBIGUOUS",
                reason=(
                    "Risk name and description are supported by evidence."
                    if has_description
                    else "Risk name is supported but description evidence is insufficient."
                ),
                evidence_refs=list(risk.evidence_refs),
            )
        )


def _extract_aum(chunks: list[Chunk]) -> list[AumItem]:
    pattern = re.compile(
        r"(?:이\s*(?:투자신탁|펀드|집합투자기구).{0,20})?(?:운용규모|순자산총액)\s*[:：]\s*([\d,\.]+)\s*(억원|백만원|원)?"
    )
    for chunk in chunks:
        text = chunk.text or ""
        if "운용전문" in text[:160] or "동종집합투자기구" in text[:240]:
            continue
        match = pattern.search(text[:400])
        if not match:
            continue
        if "해당사항 없음" in text[:80] or "해당없음" in text[:80].replace(" ", ""):
            continue
        value = float(match.group(1).replace(",", ""))
        unit = match.group(2) or "억원"
        return [
            AumItem(
                value=value,
                currency="KRW",
                unit=unit,
                evidence_refs=[chunk.chunk_id],
            )
        ]
    return []


def _is_risk_table(table: DetectedTable) -> bool:
    if is_semantic_risk_table(table):
        return True
    blob = re.sub(r"\s+", "", " ".join(table.headers) + " ".join(" ".join(row) for row in table.rows[:3]))
    if table.page_number > 12:
        return False
    return "투자위험의주요내용" in blob or "주요투자위험" in blob or "원본손실" in blob


def _risk_section(chunk: Chunk, text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if chunk.section_type == SectionType.INVESTMENT_RISK:
        return True
    if chunk.section_type in {SectionType.PERFORMANCE, SectionType.AUM}:
        return False
    if chunk.section_type == SectionType.FEES:
        if chunk.page_start > 12:
            return False
        named = sum(1 for token in NAME_STARTERS if token in compact)
        return "주요투자위험" in compact and named >= 2
    if "투자결정시유의사항" in compact[:120] or "운용전문인력" in compact[:80]:
        return False
    if chunk.page_start > 12:
        return False
    return "투자위험의주요내용" in compact or "주요투자위험" in compact


def _split_risk_blocks(text: str) -> list[tuple[str, str]]:
    cleaned = (
        (text or "")
        .replace("변동위\n험", "변동위험")
        .replace("위\n험", "위험")
        .replace("발생위\n험", "발생위험")
    )
    start = None
    for marker in ("투자위험의 주요내용", "투자위험의주요내용", "주요 투자위험", "주요투자위험"):
        idx = cleaned.find(marker)
        if idx >= 0:
            start = idx + len(marker)
            break
    body = cleaned[start:] if start is not None else cleaned
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines()]
    lines = [line for line in lines if line]
    blocks: list[tuple[str, str]] = []
    name_parts: list[str] = []
    desc_parts: list[str] = []

    def flush() -> None:
        nonlocal name_parts, desc_parts
        name = _clean_risk_name(" ".join(name_parts))
        desc = _clean_prose(" ".join(desc_parts))
        if name and desc:
            blocks.append((name, desc))
        name_parts, desc_parts = [], []

    for line in lines:
        compact = re.sub(r"\s+", "", line)
        if any(compact.startswith(re.sub(r"\s+", "", stop)) for stop in STOP_HEADINGS):
            break
        if re.match(r"^\d+\..*(?:집합투자기구의)?투자위험$", compact):
            continue
        if line in {"구분", "투자위험의 주요내용", "주요투자 위험", "주요투자위험"}:
            continue
        if name_parts and not desc_parts and (_is_name_line(line) or "위험" in compact) and len(compact) <= 16:
            name_parts.append(line)
            continue
        if _is_name_start(line) and (not desc_parts or _is_name_line(line)):
            if name_parts and desc_parts:
                flush()
            name_parts = [line]
            continue
        if name_parts:
            desc_parts.append(line)
    flush()
    return blocks


NAME_STARTERS = (
    "원본손실",
    "추적오차",
    "베이시스",
    "주식가격",
    "종목위험",
    "금리변동",
    "집중투자",
    "유동성제약",
    "지수관련",
    "부도등",
)


def _is_name_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line or "")
    return len(compact) <= 24 and not compact.endswith(("다.", "음.", "다"))


def _is_name_start(line: str) -> bool:
    compact = re.sub(r"\s+", "", line or "")
    if _is_risk_name(line):
        return True
    if len(compact) > 20:
        return False
    return any(token in compact for token in NAME_STARTERS)


def _is_risk_name(text: str) -> bool:
    compact = re.sub(r"[\s\u3000]+", "", text or "")
    compact = compact.replace("ㆍ", "")
    if not compact or compact in GRADE_LABELS:
        return False
    if len(compact) > 36 or len(compact) < 4:
        return False
    if any(label in compact and compact.endswith("위험") for label in ("매우높은", "다소높은", "매우낮은")):
        return False
    return compact.endswith("위험") or compact.endswith("위험등") or (
        "위험" in compact and any(token in compact for token in ("원본", "추적", "베이시스", "종목", "금리", "유동성", "환매", "집중", "부도", "지수"))
    )


def _normalize_risk(name: str | None, description: str | None, refs: list[str]) -> InvestmentRiskItem | None:
    clean_name = _clean_risk_name(name or "")
    clean_desc = _clean_prose(description or "")
    if not clean_name or not clean_desc:
        return None
    if len(clean_desc) < 40:
        return None
    if any(token in clean_desc for token in POINTER_ONLY) and len(clean_desc) < 80:
        return None
    return InvestmentRiskItem(name=clean_name, description=clean_desc, evidence_refs=list(refs))


def _clean_risk_name(name: str) -> str:
    compact = re.sub(r"\s+", "", name or "")
    compact = re.sub(r"^(구분|주요투자위험|투자위험의주요내용)", "", compact)
    if (
        not compact
        or compact[0].isdigit()
        or compact.startswith("가.")
        or "구분" in compact
    ):
        return ""
    if len(compact) > 28 or len(compact) < 4:
        return ""
    if not re.search(r"위험(등)?(?:\([^)]+\))?$", compact):
        return ""
    if compact in GENERIC_RISK_NAMES:
        return ""
    return _format_risk_name(compact)


def _format_risk_name(compact: str) -> str:
    known = {
        "원본손실위험등": "원본손실 위험 등",
        "원본손실위험": "원본손실 위험",
        "투자원본손실위험": "투자원본 손실위험",
        "추적오차발생위험": "추적오차 발생위험",
        "베이시스위험": "베이시스 위험",
        "주식가격변동위험": "주식가격 변동위험",
        "금리변동위험": "금리 변동위험",
        "부도등의위험": "부도등의 위험",
        "집중투자에따른위험(종목)": "집중투자에 따른 위험(종목)",
        "집중투자에따른위험(섹터)": "집중투자에 따른 위험(섹터)",
        "투자자금회수위험(유동성위험)": "투자자금 회수위험(유동성위험)",
    }
    if compact in known:
        return known[compact]
    compact = compact.replace("에따른", "에 따른")
    if compact.endswith("위험등"):
        return compact[:-3] + " 위험 등"
    return compact


def _dedupe_risks(items: list[InvestmentRiskItem]) -> list[InvestmentRiskItem]:
    kept: list[InvestmentRiskItem] = []
    for item in items:
        compact = re.sub(r"\s+", "", item.name or "")
        desc = (item.description or "")[:80]
        if any(re.sub(r"\s+", "", other.name or "").endswith(compact) and other.name != item.name for other in items):
            continue
        if any((other.description or "")[:80] == desc and other is not item for other in kept):
            continue
        kept.append(item)
    return kept


def _strip_heading(text: str, headings: tuple[str, ...] | None = None) -> str:
    body = (text or "").strip()
    replaced = HEADING_RE.sub("", body, count=1)
    if replaced != body:
        body = replaced.lstrip(" \n:：")
    else:
        for heading in headings or ():
            if body.startswith(heading):
                body = body[len(heading) :].lstrip(" \n:：")
                break
    body = SUBHEAD_RE.sub("", body, count=1).lstrip(" \n:：")
    if body.startswith("및 ") or body.startswith("및\n"):
        body = body[1:].lstrip(" \n")
    return body.strip()


def _first_sentences(text: str, limit: int = 420) -> str:
    return _clip_sentence(text, limit=limit)


def _looks_like_purpose(text: str) -> bool:
    body = _purpose_body(text)
    if not body or len(body) < 24:
        return False
    compact_body = _compact(body)
    return (
        any(compact_body.startswith(_compact(prefix)) for prefix in PURPOSE_PREFIXES)
        or _is_objective_label_value(body)
    )


def _purpose_body(text: str | None) -> str:
    """Remove layout bullets and row prefixes before semantic role checks."""
    return re.sub(
        r"^(?:(?:\d+\.)|[-–—ㆍ·▪•▶▷●○□◇※*])+\s*",
        "",
        _clean_prose(text or ""),
    ).strip()


def _is_objective_label_value(text: str | None) -> bool:
    """Recognize source-labelled, noun-style objective values deterministically."""
    compact = _compact(_purpose_body(text))
    if len(compact) < 24 or any(marker in compact for marker in DISCLAIMER_MARKERS):
        return False
    objective_signal = any(token in compact for token in (
        "자본이득", "자본소득", "이자수익", "이자소득", "배당소득",
        "투자수익", "초과수익", "안정적인수익", "수익을추구",
    ))
    objective_end = compact.endswith((
        "추구", "추구함", "목적으로함", "목적으로합니다", "목표로합니다",
    ))
    source_subject = compact.startswith((
        "투자대상인", "집합투자증권", "유가증권", "이투자신탁",
        "이집합투자기구", "본투자신탁", "국공채", "채권", "주식",
    ))
    return objective_signal and objective_end and source_subject


def _clean_prose(text: str) -> str:
    cleaned = re.sub(r"[ \t]+", " ", text or "")
    cleaned = re.sub(r"\n+", " ", cleaned)
    return cleaned.strip()
