"""Question classification and deterministic calculation-spec parsing."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from .calculation_worker import CalculationQuerySpec


CALCULATION_MARKERS = (
    "세액공제", "납입한도", "납입 한도", "소득공백", "소득 공백", "노후소득", "부족액",
    "ISA", "만기 ISA",
)
CALC_POLICY_MARKERS = ("이월", "신청 절차", "전환 신청")
CALC_AMOUNT_MARKERS = ("얼마", "환급", "계산", "최대 한도", "총 한도", "맞나요", "얼마까지", "합쳐서")

# Supports: 1,500만원 / 1,500만 원 / 1500만원을 / 900만 원
_AMOUNT_RE = re.compile(
    r"(?P<amount>-?\d{1,3}(?:,\d{3})+|\d+)(?:\s*만)?\s*(?P<unit>만원|원)"
)


def is_numeric_calculation_question(question: str) -> bool:
    if not any(marker in question for marker in CALCULATION_MARKERS):
        return False
    if any(marker in question for marker in CALC_POLICY_MARKERS) and not any(
        marker in question for marker in CALC_AMOUNT_MARKERS
    ):
        return False
    return True


def _amount(token: str, unit: str, *, man_suffix: bool) -> Decimal:
    value = Decimal(token.replace(",", ""))
    if unit == "만원" or man_suffix:
        return value * Decimal("10000")
    return value


def _iter_amounts(question: str) -> list[tuple[int, Decimal, str]]:
    """Return (start_index, amount_won, local_context)."""
    found: list[tuple[int, Decimal, str]] = []
    for match in re.finditer(
        r"(?P<amount>-?\d{1,3}(?:,\d{3})+|\d+)\s*(?P<unit>만\s*원|만원|원)",
        question,
    ):
        unit = re.sub(r"\s+", "", match.group("unit"))
        value = Decimal(match.group("amount").replace(",", ""))
        if unit.startswith("만"):
            value *= Decimal("10000")
        start = match.start()
        ctx = question[max(0, start - 12): match.end()]
        found.append((start, value, ctx))
    return found


def classify(question: str) -> str | None:
    if not is_numeric_calculation_question(question):
        return None
    q = question.lower()
    if "세액공제" in q or "환급" in q or "isa" in q:
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
    amounts = _iter_amounts(question)
    if any(value < 0 for _, value, _ in amounts):
        return {"status": "INVALID_INPUT", "field": "amount"}

    salary: Decimal | None = None
    contribution: Decimal | None = None
    for _, value, ctx in amounts:
        if any(token in ctx for token in ("총급여", "급여", "소득", "연봉")):
            salary = value
        elif any(token in ctx for token in ("납입", "넣", "이체", "전환")):
            contribution = value

    # If roles are ambiguous but two amounts exist with salary wording elsewhere.
    if salary is None and "총급여" in question and amounts:
        # Prefer the amount nearest to 총급여.
        for _, value, ctx in amounts:
            if "총급여" in question[max(0, question.find("총급여") - 2): question.find("총급여") + 30]:
                # pick first amount after 총급여 marker
                pass
        idx = question.find("총급여")
        near = [value for pos, value, _ in amounts if idx <= pos <= idx + 40]
        if near:
            salary = near[0]
    if contribution is None and any(token in question for token in ("납입했", "납입했습니다", "납입하면", "넣었")):
        # contribution is typically the non-salary amount
        candidates = [value for _, value, ctx in amounts if salary is None or value != salary]
        if candidates:
            contribution = candidates[0] if salary is not None else (candidates[-1] if len(candidates) > 1 else candidates[0])

    limit_question = any(
        token in question for token in ("얼마까지", "최대", "한도", "다 합쳐서", "합쳐서", "각각 얼마")
    ) and not any(token in question for token in ("납입했", "넣었", "납입했습니다", "환급받을", "맞나요"))

    isa_transfer = any(token in question for token in ("ISA", "만기 ISA", "ISA 만기")) and any(
        token in question for token in ("전환", "이체", "납입", "세액공제")
    )
    if isa_transfer:
        transfer_amount = None
        for _, value, ctx in amounts:
            if any(t in ctx for t in ("ISA", "전환", "이체", "만기")):
                transfer_amount = value
                break
        if transfer_amount is None and amounts:
            transfer_amount = amounts[0][1]
        if transfer_amount is None:
            return {"status": "CLARIFY", "missing_inputs": ["isa_transfer_amount"]}
        inputs: dict[str, Any] = {
            "mode": "isa_transfer",
            "isa_transfer_amount": str(transfer_amount),
        }
        return CalculationQuerySpec(
            calculation_type="tax_credit",
            policy_year=year,
            required_inputs=["isa_transfer_amount"],
            provided_inputs=inputs,
        )

    if limit_question or (contribution is None and salary is None and not amounts):
        return CalculationQuerySpec(
            calculation_type="tax_credit",
            policy_year=year,
            required_inputs=[],
            provided_inputs={"mode": "limit_summary"},
        )

    if contribution is None and amounts and salary is None:
        contribution = amounts[0][1]
    if contribution is None and amounts and salary is not None:
        others = [value for _, value, _ in amounts if value != salary]
        contribution = others[0] if others else None

    if contribution is None:
        return CalculationQuerySpec(
            calculation_type="tax_credit",
            policy_year=year,
            required_inputs=[],
            provided_inputs={"mode": "limit_summary"},
        )

    inputs: dict[str, Any] = {
        "contribution_amount": str(contribution),
        "mode": "amount",
    }
    if salary is not None:
        inputs["gross_salary"] = str(salary)
    if any(token in question for token in ("맞나요", "맞는가요", "맞습니까")):
        inputs["premise_check"] = "true"
        claimed = re.search(r"(\d+(?:\.\d+)?)\s*%", question)
        if claimed:
            inputs["claimed_rate_percent"] = claimed.group(1)
        claimed_refund = [
            value for _, value, ctx in amounts if any(t in ctx for t in ("환급", "약 "))
        ]
        if claimed_refund:
            inputs["claimed_credit_amount"] = str(claimed_refund[0])

    return CalculationQuerySpec(
        calculation_type="tax_credit",
        policy_year=year,
        required_inputs=["contribution_amount"],
        provided_inputs=inputs,
    )


def income_gap_spec(question: str) -> CalculationQuerySpec | dict[str, Any]:
    amounts = [
        (pos, value)
        for pos, value, _ in _iter_amounts(question)
    ]
    if any(value < 0 for _, value in amounts):
        return {"status": "INVALID_INPUT", "field": "amount"}
    target = next(
        (
            value
            for pos, value in amounts
            if any(word in question[max(0, pos - 30):pos] for word in ("목표", "필요"))
        ),
        None,
    )
    expected = next(
        (
            value
            for pos, value in amounts
            if any(word in question[max(0, pos - 30):pos] for word in ("예상", "연금"))
        ),
        None,
    )
    if target is None or expected is None:
        return {
            "status": "CLARIFY",
            "missing_inputs": [
                name
                for name, value in (("target_income", target), ("expected_income", expected))
                if value is None
            ],
        }
    return CalculationQuerySpec(
        calculation_type="income_gap",
        policy_year=0,
        required_inputs=["target_income", "expected_income"],
        provided_inputs={"target_income": str(target), "expected_income": str(expected)},
    )
