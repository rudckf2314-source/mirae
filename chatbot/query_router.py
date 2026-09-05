from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable

from .calculation_gateway import is_numeric_calculation_question


PRODUCT_KEYWORDS = {
    "상품", "펀드", "ETF", "ETN", "리츠", "수수료", "보수", "총보수",
    "수익률", "위험등급", "위험 등급", "운용사", "클래스", "비용",
    "추천", "비교", "가장 낮", "가장 높", "상위",
    "투자설명서", "투자위험", "개별위험", "운용보수",
}

PRODUCT_SEARCH_KEYWORDS = {
    "추천", "비교", "가장 낮", "가장 높", "상위", "저렴", "대조",
    "낮은 순", "높은 순", "오름차순", "내림차순",
}

PROSPECTUS_CONCEPT_KEYWORDS = {
    "투자설명서", "총보수", "합성", "모자형", "자펀드", "모투자신탁",
    "자신탁", "피투자",
}

FUND_NAME_MARKERS = (
    "증권자투자신탁",
    "증권 투자신탁",
    "증권투자신탁",
    "자투자신탁",
)

OPS_KEYWORDS = {
    "디폴트옵션",
    "실물이전",
    "실물 이전",
    "감독규정",
    "적용 통지",
    "불가사유",
    "불가 사유",
    "불가사유코드",
    "자동 매수",
    "사전지정",
}

_NAME_SUFFIXES = (
    "증권자투자신탁",
    "증권투자신탁",
    "자투자신탁",
    "투자신탁",
    "증권펀드",
)
_GENERIC_HINTS = {
    "주식", "채권", "펀드", "증권", "종류", "클래스", "투자신탁", "미래에셋",
}

_QUERY_TAILS = tuple(
    sorted(
        (
            "알려주세요", "알려줘", "설명해줘", "설명해주세요",
            "무엇인가", "무엇인가요", "뭐야",
            "총보수율", "총보수", "보수율", "운용보수",
            "수수료율", "수수료", "수익률",
            "위험등급", "위험 등급",
            "클래스", "비교해줘", "비교", "얼마야", "얼마인가요", "얼마",
        ),
        key=lambda item: -len(item),
    )
)

DOCUMENT_KEYWORDS = {
    "DB", "DC", "IRP", "퇴직연금", "연금저축", "세액공제", "중도인출",
    "연금수령", "퇴직급여", "가입", "이전", "제도", "조건", "법정",
    "가능", "불가", "차이", "무엇", "어떻게",
}

LAW_KEYWORDS = {
    "중도인출", "중도 인출", "법률", "법령", "시행령", "시행규칙",
    "조문", "법적", "법적 근거", "근거 규정", "법에서", "법상",
    "감독규정", "규정상", "규정 요건", "한도 제한",
}

EXPLICIT_LAW_KEYWORDS = {
    "법률", "법령", "시행령", "시행규칙", "조문", "법적",
    "법적 근거", "근거 규정", "법에서", "법상",
}

EXPLANATION_KEYWORDS = {
    "설명", "의미", "제도", "쉽게", "알려줘", "알려 주세요",
}

_TOOL_ORDER = ("document", "product", "law", "calculation")


@dataclass
class RouteDecision:
    tools: list[str]
    reason: str

    @property
    def route(self) -> str:
        """기존 document / product / both 소비자와의 호환성을 유지합니다."""
        if self.tools == ["document", "product"]:
            return "both"
        if len(self.tools) == 1:
            return self.tools[0]
        return "+".join(self.tools)


def _fold(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _contains_any(question: str, keywords: Iterable[str]) -> int:
    q = question.upper()
    return sum(1 for key in keywords if key.upper() in q)


def has_specific_fund_name(question: str) -> bool:
    """Quoted or legal fund-name stems, not generic 상품/펀드 nouns."""
    return _contains_any(question, FUND_NAME_MARKERS) > 0


def product_search_hints(records: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    """Schema product names/codes the router can treat as a prospectus question."""
    hints: set[str] = set()
    for record in records:
        names = [record.get("product_name"), record.get("source_file")]
        for raw in names:
            folded = _fold(raw)
            if not folded:
                continue
            stem = _fold(Path(str(raw)).stem)
            hints.add(folded)
            hints.add(stem)
            stripped = stem
            for suffix in _NAME_SUFFIXES:
                stripped = stripped.replace(suffix.casefold(), "")
            stripped = re.sub(r"[\(\)\[\]\{\}]", "", stripped)
            hints.add(stripped)
            # Add distinctive product-name aliases so catalog products such as
            # "솔로몬 국공채 ..." outrank generic document routing.
            for token in re.findall(r"[가-힣A-Za-z0-9]+", str(raw)):
                folded_token = _fold(token)
                if len(folded_token) >= 3 and folded_token not in _GENERIC_HINTS and folded_token not in {"미래에셋", "증권", "투자신탁", "자투자신탁", "펀드", "클래스"}:
                    hints.add(folded_token)
        for code in (
            record.get("product_kofia_fund_code"),
            record.get("class_kofia_fund_code"),
        ):
            folded = _fold(code)
            if folded:
                hints.add(folded)
        source_file = str(record.get("source_file") or "")
        stem = Path(source_file).stem
        if stem:
            hints.add(_fold(stem))
            if stem.upper().startswith("R2_"):
                hints.add(_fold(stem[3:]))
    return tuple(
        hint
        for hint in hints
        if len(hint) >= 3 and hint not in _GENERIC_HINTS
    )


def _ordered_tools(tools: list[str]) -> list[str]:
    ordered = [name for name in _TOOL_ORDER if name in tools]
    ordered.extend(name for name in tools if name not in ordered)
    return ordered


class QueryRouter:
    """질문에 필요한 document / product / law Tool 목록을 선택합니다."""

    def __init__(self, product_hints: Iterable[str] = ()) -> None:
        self.product_hints = tuple(hint for hint in product_hints if hint)

    def mentions_prospectus_product(self, question: str) -> bool:
        folded = _fold(question)
        if any(hint in folded for hint in self.product_hints):
            return True
        stripped = folded
        for tail in _QUERY_TAILS:
            stripped = stripped.replace(_fold(tail), "")
        if stripped and any(hint in stripped for hint in self.product_hints):
            return True
        if len(stripped) >= 6 and any(stripped in hint for hint in self.product_hints):
            return True
        return len(folded) >= 6 and any(folded in hint for hint in self.product_hints)

    def mentions_named_product(self, question: str) -> bool:
        return self.mentions_prospectus_product(question) or has_specific_fund_name(question)

    def decide(self, question: str) -> RouteDecision:
        if is_numeric_calculation_question(question):
            return RouteDecision(tools=["calculation"], reason="deterministic_calculation")

        named_product = self.mentions_named_product(question)
        ops_hits = _contains_any(question, OPS_KEYWORDS)
        law_hits = _contains_any(question, LAW_KEYWORDS)
        explicit_law_hits = _contains_any(question, EXPLICIT_LAW_KEYWORDS)
        explanation_hits = _contains_any(question, EXPLANATION_KEYWORDS)
        search_hits = _contains_any(question, PRODUCT_SEARCH_KEYWORDS)
        concept_hits = _contains_any(question, PROSPECTUS_CONCEPT_KEYWORDS)
        product_noun_hits = _contains_any(question, PRODUCT_KEYWORDS)
        document_hits = _contains_any(question, DOCUMENT_KEYWORDS)

        tools: list[str] = []
        reasons: list[str] = []

        if law_hits > 0:
            tools.append("law")
            reasons.append("법령·인출 조건 등 현행 법령 확인 요소가 포함되었습니다.")

        if named_product:
            tools.append("product")
            reasons.append("특정 상품명/문서가 있어 투자설명서 스키마 조회가 필요합니다.")
        elif ops_hits > 0:
            tools.append("document")
            reasons.append("디폴트옵션·실물이전·감독규정 등 제도/업무 안내가 중심입니다.")
        elif search_hits > 0:
            tools.append("product")
            reasons.append("상품 비교·추천·순위 조회가 필요한 질문입니다.")
        elif concept_hits > 0:
            tools.append("document")
            reasons.append("특정 펀드명 없는 투자설명서·비용 구조 설명입니다.")
        elif product_noun_hits > 0:
            tools.append("product")
            reasons.append("투자설명서 스키마(상품 DB) 조회가 필요한 질문입니다.")
        elif document_hits > 0 or not tools:
            tools.append("document")
            reasons.append("연금 제도·절차·조건 설명 중심의 질문입니다.")

        if (
            law_hits > 0
            and explicit_law_hits == 0
            and explanation_hits > 0
            and "product" not in tools
            and "document" not in tools
        ):
            tools.insert(0, "document")
            reasons.append("법적 요건과 함께 제도의 일반적인 설명이 필요한 질문입니다.")

        return RouteDecision(tools=_ordered_tools(tools), reason=" ".join(reasons))
