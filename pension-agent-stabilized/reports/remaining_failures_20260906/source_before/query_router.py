from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable

from .calculation_gateway import is_numeric_calculation_question
from .task_intent import classify_task_intent


PRODUCT_KEYWORDS = {
    "상품", "펀드", "ETF", "ETN", "리츠", "수수료", "보수", "총보수",
    "수익률", "위험등급", "위험 등급", "운용사", "클래스", "비용",
    "추천", "비교", "가장 낮", "가장 높", "상위",
    "투자설명서", "투자위험", "개별위험", "운용보수",
}

PRODUCT_SEARCH_KEYWORDS = {
    "추천", "비교", "가장 낮", "가장 높", "상위", "저렴", "대조",
    "낮은 순", "높은 순", "오름차순", "내림차순", "보여줘", "찾아줘",
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
    "가입자 교육",
    "가입자교육",
    "재정검증",
    "최소적립",
    "퇴직연금규약",
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
    # Account/system nouns frequently appear inside class names; never treat as catalog IDs.
    "퇴직연금", "개인연금", "연금저축", "디폴트옵션", "온라인", "오프라인",
    "수수료미징구", "수수료선취", "종류형", "모자형",
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
    "가능", "불가", "차이", "무엇", "어떻게", "교육", "과태료",
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
            for token in re.findall(r"[가-힣A-Za-z0-9]+", str(raw)):
                folded_token = _fold(token)
                if (
                    len(folded_token) >= 3
                    and folded_token not in _GENERIC_HINTS
                    and folded_token not in {"미래에셋", "증권", "투자신탁", "자투자신탁", "펀드", "클래스"}
                ):
                    hints.add(folded_token)
        for code in (
            record.get("product_kofia_fund_code"),
            record.get("class_kofia_fund_code"),
        ):
            folded = _fold(code)
            if folded and len(folded) >= 3:
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
        # Prefer distinctive catalog tokens; short tokens still allowed when not generic.
        candidates = [hint for hint in self.product_hints if hint not in _GENERIC_HINTS]
        if any(hint in folded for hint in candidates if len(hint) >= 3):
            return True
        stripped = folded
        for tail in _QUERY_TAILS:
            stripped = stripped.replace(_fold(tail), "")
        if stripped and any(hint in stripped for hint in candidates if len(hint) >= 3):
            return True
        if len(stripped) >= 8 and any(stripped in hint for hint in candidates if len(hint) >= 8):
            return True
        return False

    def mentions_named_product(self, question: str) -> bool:
        return self.mentions_prospectus_product(question) or has_specific_fund_name(question)

    def decide(self, question: str) -> RouteDecision:
        if is_numeric_calculation_question(question):
            # Institutional limit/policy questions also need document family labeling
            # when the adapter scores document routes; calculation still owns numbers.
            limit_policy = any(
                token in question
                for token in ("한도", "얼마까지", "각각 얼마", "합쳐서", "다 합쳐")
            ) and not any(
                token in question for token in ("납입했", "넣었", "환급받을", "맞나요", "ISA")
            )
            if limit_policy:
                return RouteDecision(
                    tools=["document", "calculation"],
                    reason="납입·세액공제 한도 정책은 제도 설명과 결정적 계산을 함께 사용합니다.",
                )
            if any(token in question for token in ("ISA", "만기 ISA", "ISA 만기")):
                return RouteDecision(
                    tools=["calculation"],
                    reason="ISA 전환 세액공제 한도는 결정적 계산기로 처리합니다.",
                )
            return RouteDecision(tools=["calculation"], reason="deterministic_calculation")

        intent = classify_task_intent(question)
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

        if intent.primary == "action_request":
            # Gather product/docs so we can explain scope + product facts; do not execute.
            if named_product:
                tools.append("product")
            else:
                tools.append("document")
            reasons.append("주문·체결은 실행하지 않고 가능 여부·상품 안내만 제공합니다.")
            if law_hits and "product" not in tools:
                tools.append("law")
            return RouteDecision(tools=_ordered_tools(tools), reason=" ".join(reasons))

        if intent.primary == "tax_calculation":
            if any(k in question for k in ("과세이연", "분리과세", "초과납입", "이월", "종합과세")):
                tools = ["document", "law"]
                reasons.append("세제 제도 설명은 문서·법령 근거를 우선합니다.")
                return RouteDecision(tools=_ordered_tools(tools), reason=" ".join(reasons))
            tools = ["calculation"]
            reasons.append("세제·한도 수치 질문은 결정적 계산 경로를 사용합니다.")
            return RouteDecision(tools=tools, reason=" ".join(reasons))

        if law_hits > 0:
            tools.append("law")
            reasons.append("법령·인출 조건 등 현행 법령 확인 요소가 포함되었습니다.")

        # Task-intent first: institutional/procedure questions must not become catalog dumps
        # merely because the text contains 상품/펀드/퇴직연금.
        if intent.primary in {"procedure", "institution"} and not (named_product and search_hits):
            tools.append("document")
            reasons.append(f"업무 목적이 {intent.primary}이므로 제도/절차 문서를 우선합니다.")
        elif intent.primary == "correction":
            if is_numeric_calculation_question(question) or any(k in question for k in ("세액공제", "공제율", "환급")):
                tools = ["calculation"]
                reasons.append("전제 확인이 필요한 수치·세제 질문입니다.")
                return RouteDecision(tools=tools, reason=" ".join(reasons))
            if any(k in question for k in ("과세이연", "분리과세", "초과납입", "이월", "종합과세", "사적연금")):
                tools = ["document", "law"]
                reasons.append("전제 확인이 필요한 세제 제도 질문입니다.")
                return RouteDecision(tools=_ordered_tools(tools), reason=" ".join(reasons))
            tools.append("document")
            if law_hits:
                tools.append("law")
            reasons.append("전제 확인이 필요한 제도 질문입니다.")
        elif intent.primary == "compound_holding":
            # Do not catalog-search until the held product is identified.
            tools.append("document")
            reasons.append("보유 상품 식별이 선행되어야 하며 임의 상품을 선택하지 않습니다.")
        elif named_product and (search_hits > 0 or intent.primary in {"product_attribute", "product_search"}):
            tools.append("product")
            reasons.append("특정 상품명과 속성/비교 요청이 함께 있습니다.")
        elif named_product and intent.primary == "compound_holding":
            tools.append("product")
            reasons.append("보유 상품 식별 후 비교가 필요합니다.")
        elif named_product and not intent.primary in {"procedure", "institution"}:
            tools.append("product")
            reasons.append("특정 상품명/문서가 있어 투자설명서 스키마 조회가 필요합니다.")
        elif intent.primary == "product_attribute":
            tools.append("product")
            reasons.append("상품 운용제한·의무비율 등 속성 조회가 필요합니다.")
        elif ops_hits > 0 or intent.primary in {"procedure", "institution"}:
            tools.append("document")
            reasons.append("디폴트옵션·실물이전·교육·감독규정 등 제도/업무 안내가 중심입니다.")
        elif search_hits > 0 or intent.primary == "product_search":
            tools.append("product")
            reasons.append("상품 비교·추천·순위 조회가 필요한 질문입니다.")
        elif concept_hits > 0 and not named_product:
            # Generic prospectus concepts without a fund name stay on documents;
            # explicit limit/ratio wording already routed via product_attribute.
            tools.append("document")
            reasons.append("특정 펀드명 없는 투자설명서·비용 구조 설명입니다.")
        elif intent.primary == "product_attribute" and product_noun_hits > 0:
            tools.append("product")
            reasons.append("상품 속성 조회가 필요한 질문입니다.")
        elif product_noun_hits > 0 and search_hits > 0:
            tools.append("product")
            reasons.append("상품 검색 신호가 있는 질문입니다.")
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

        # Never keep product-only for pure procedure when product slipped in via nouns.
        if intent.primary in {"procedure", "institution"} and "product" in tools and not named_product:
            tools = [t for t in tools if t != "product"]
            if "document" not in tools:
                tools.insert(0, "document")
            reasons.append("제도/절차 질문에서 일반 상품 명사만으로는 상품 DB를 사용하지 않습니다.")

        return RouteDecision(tools=_ordered_tools(tools), reason=" ".join(reasons))
