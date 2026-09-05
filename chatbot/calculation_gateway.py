"""Question classification and deterministic calculation-spec parsing."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from .calculation_worker import CalculationQuerySpec


CALCULATION_MARKERS = (
    "세액공제", "납입한도", "납입 한도", "소득공백", "소득 공백", "노후소득", "부족액",
)
CALC_POLICY_MARKERS = ("이월", "신청 절차", "전환 신청")
CALC_AMOUNT_MARKERS = ("얼마", "환급", "계산", "최대 한도", "총 한도", "맞나요", "얼마까지", "합쳐서")


def is_numeric_calculation_question(question: str) -> bool:
    if not any(marker in question for marker in CALCULATION_MARKERS):
        return False
    if any(marker in question for marker in CALC_POLICY_MARKERS) and not any(marker in question for marker in CALC_AMOUNT_MARKERS):
        return False
    return True


def _amount(token: str, unit: str) -> Decimal:
    value = Decimal(token.replace(",", ""))
    return value * (Decimal("10000") if unit == "만원" else Decimal("1"))


def classify(question: str) -> str | None:
    if not is_numeric_calculation_question(question):
        return None
    q = question.lower()
    if "세액공제" in q:
        return "tax_credit"
    if "납입" in q and "한도" in q:
        return "contribution_limit"
    if any(word in q for word in ("소득 공백", "소득공백", "부족액", "목표 소득", "노후소득")):
        return "income_gap"
    return None


def policy_year_from_question(question: str, default: int | None = None) -> int:
    match = re.search(r"(20\d{2})\s*년", question)
    if match:
        return int(match.group(1))
    return default or datetime.now().year


def tax_credit_spec(question: str, *, policy_year: int | None = None) -> CalculationQuerySpec | dict[str, Any]:
    year = policy_year_from_question(question, policy_year)
    amounts = [
        _amount(match.group("amount"), match.group("unit"))
        for match in re.finditer(r"(?P<amount>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>만원|원)", question)
    ]
    if any(value < 0 for value in amounts):
        return {"status": "INVALID_INPUT", "field": "amount"}

    # Limit-only questions are answerable from policy without inventing a user contribution.
    limit_question = any(token in question for token in ("얼마까지", "최대", "한도", "다 합쳐서", "합쳐서")) and not any(
        token in question for token in ("납입했", "넣었", "내면", "납입하면", "환급")
    )
    if limit_question or not amounts:
        return CalculationQuerySpec(
            calculation_type="tax_credit",
            policy_year=year,
            required_inputs=[],
            provided_inputs={"mode": "limit_summary"},
        )

    contribution = amounts[0]
    return CalculationQuerySpec(
        calculation_type="tax_credit",
        policy_year=year,
        required_inputs=["contribution_amount"],
        provided_inputs={"contribution_amount": str(contribution), "mode": "amount"},
    )


def income_gap_spec(question: str) -> CalculationQuerySpec | dict[str, Any]:
    amounts = [
        (match.start(), _amount(match.group("amount"), match.group("unit")))
        for match in re.finditer(r"(?P<amount>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>만원|원)", question)
    ]
    if any(value < 0 for _, value in amounts):
        return {"status": "INVALID_INPUT", "field": "amount"}
    target = next((value for pos, value in amounts if any(word in question[max(0, pos - 30):pos] for word in ("목표", "필요"))), None)
    expected = next((value for pos, value in amounts if any(word in question[max(0, pos - 30):pos] for word in ("예상", "연금"))), None)
    if target is None or expected is None:
        return {"status": "CLARIFY", "missing_inputs": [name for name, value in (("target_income", target), ("expected_income", expected)) if value is None]}
    return CalculationQuerySpec(
        calculation_type="income_gap", policy_year=0,
        required_inputs=["target_income", "expected_income"],
        provided_inputs={"target_income": str(target), "expected_income": str(expected)},
    )
