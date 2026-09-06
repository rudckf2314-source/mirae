"""Deterministic semantic task intents for routing (no Test-id hardcoding)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskIntent:
    """Coarse work-type the user is asking the agent to perform."""

    primary: str
    secondary: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


# Institutional / procedure work — must not collapse to product catalog listing.
PROCEDURE_MARKERS = (
    "실물이전", "실물 이전", "디폴트옵션", "사전지정", "적용 통지", "옵트인",
    "자동 매수", "자동매수", "자동 재예치", "불가사유", "가입자 교육", "가입자교육",
    "과태료", "퇴직연금규약", "규약", "재정검증", "최소적립", "평균임금",
    "중도인출", "담보제공", "지연이자", "통지",
)

INSTITUTION_EXPLAIN_MARKERS = (
    "제도", "절차", "요건", "자격", "가능한가", "가능한가요",
    "안 되나", "안되나요", "왜 안", "어떻게 다르", "차이",
)

PRODUCT_ATTRIBUTE_MARKERS = (
    "총보수", "보수율", "수수료", "수익률", "위험등급", "위험 등급",
    "클래스", "투자전략", "모펀드", "편입", "순자산",
    "위험평가액", "투자 의무", "의무 비율", "운용 한도", "투자한도", "편입비율",
)

PRODUCT_SEARCH_MARKERS = (
    "추천", "보여줘", "찾아줘", "비교해", "낮은 순", "높은 순",
    "가장 낮", "가장 높", "상위", "저렴",
)

# Imperative execution only — do not match narrative phrases like "주문 처리가 끝난".
ORDER_MARKERS = (
    "매수 주문 처리", "지금 즉시 매수", "즉시 매수 주문",
    "매수해 주세요", "매수해주세요", "매수 주문해", "주문해 주세요", "주문해주세요",
    "체결해 주세요", "체결해주세요", "매도 주문해", "매도해 주세요",
)

CORRECTION_MARKERS = (
    "맞나요", "맞는가요", "맞습니까", "틀린가", "아닌가요", "아닌가",
    "무조건",
)
# Absolute claim wording alone is not a false-premise signal.
ABSOLUTE_CLAIM_MARKERS = ("전액", "100%")


HOLDING_WITHOUT_ID_MARKERS = (
    "보유한", "보유 중", "운용 중", "운용중인", "제 계좌", "내 계좌",
)


def classify_task_intent(question: str) -> TaskIntent:
    q = question or ""
    reasons: list[str] = []
    secondary: list[str] = []

    if any(m in q for m in ORDER_MARKERS):
        return TaskIntent("action_request", reasons=("execution_or_order_request",))

    procedure = any(m in q for m in PROCEDURE_MARKERS)
    institution = any(m in q for m in INSTITUTION_EXPLAIN_MARKERS)
    attr = any(m in q for m in PRODUCT_ATTRIBUTE_MARKERS)
    search = any(m in q for m in PRODUCT_SEARCH_MARKERS)
    correction = any(m in q for m in CORRECTION_MARKERS)
    holding = any(m in q for m in HOLDING_WITHOUT_ID_MARKERS)

    # Holding + compare/search without a concrete fund identity → clarify first.
    if holding and (search or attr) and not any(m in q for m in ("증권자투자신탁", "증권투자신탁", "자투자신탁")):
        return TaskIntent("compound_holding", reasons=("holding_plus_compare",))

    # False-premise checks must win over prospectus-attribute keyword collisions
    # (e.g. 투자설명서 + 총보수율 + 맞나요 → correction, not product catalog).
    if correction or (
        any(m in q for m in ABSOLUTE_CLAIM_MARKERS)
        and any(m in q for m in ("맞나요", "맞는가요", "맞습니까", "무조건"))
    ):
        reasons.append("premise_check_signal")
        secondary.append("calculation_or_rule")
        return TaskIntent("correction", tuple(secondary), tuple(reasons))

    # Prospectus limit/ratio lookups must win over generic institution wording.
    if any(
        k in q
        for k in ("위험평가액", "투자 의무", "의무 비율", "운용 한도", "투자한도", "편입비율")
    ) or (attr and any(m in q for m in ("증권자투자신탁", "증권투자신탁", "자투자신탁", "투자설명서", "약관"))):
        return TaskIntent("product_attribute", reasons=("prospectus_limit_or_ratio",))

    # Procedure/rules about funds (e.g. small-fund in-kind transfer) are not catalog search.
    if procedure:
        reasons.append("procedure_or_ops_rule")
        if attr and search:
            secondary.append("product_compare")
        return TaskIntent("procedure", tuple(secondary), tuple(reasons))

    if search and not institution:
        return TaskIntent("product_search", reasons=("explicit_search_or_rank",))

    if attr and not institution:
        return TaskIntent("product_attribute", reasons=("product_metric_lookup",))

    if holding and search:
        return TaskIntent("compound_holding", reasons=("holding_plus_compare",))

    if any(
        k in q
        for k in (
            "세액공제", "납입한도", "납입 한도", "환급", "과세이연", "분리과세",
            "초과납입", "종합과세", "ISA", "만기 ISA", "사적연금", "연금소득",
        )
    ):
        return TaskIntent("tax_calculation", reasons=("tax_or_limit",))

    if institution or any(k in q for k in ("교육", "과태료", "최소적립", "규약")):
        return TaskIntent("institution", reasons=("institution_or_education",))

    return TaskIntent("general", reasons=("fallback_general",))
