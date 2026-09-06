from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CalculationType = Literal["tax_credit", "contribution_limit", "income_gap"]


class CalculationQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calculation_type: CalculationType
    policy_year: int
    required_inputs: list[str]
    provided_inputs: dict[str, str | int | float]
    missing_inputs: list[str] = Field(default_factory=list)
    currency: str = "KRW"
    rounding_policy: str = "KRW_HALF_UP"


class CalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calculation_type: CalculationType
    inputs: dict[str, str]
    formula_id: str
    formula_version: str
    policy_year: int
    intermediate_values: dict[str, str]
    result: str
    unit: str
    rounding_applied: str
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    policy_evidence: list[dict[str, str]] = Field(default_factory=list)


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formula_id: str
    version: str
    source: str
    tax_credit_rate: Decimal | None = None
    lower_income_tax_credit_rate: Decimal | None = None
    contribution_limit: Decimal | None = None
    pension_savings_limit: Decimal | None = None
    annual_contribution_limit: Decimal | None = None
    local_tax_surcharge_ratio: Decimal | None = None
    isa_extra_credit_base_limit: Decimal | None = None
    isa_transfer_credit_ratio: Decimal | None = None
    gross_salary_threshold: Decimal | None = None
    comprehensive_income_threshold: Decimal | None = None
    evidence_source_key: str | None = None
    evidence_article_no: str | None = None


class CalculationWorker:
    """Rule/Decimal-only worker.  No LLM-created numeric facts are permitted."""

    def __init__(self, policies: dict[int, PolicyRule] | None = None) -> None:
        self.policies = policies or {}

    def run(self, spec: CalculationQuerySpec) -> CalculationResult | dict[str, Any]:
        missing = [name for name in spec.required_inputs if name not in spec.provided_inputs]
        if missing:
            return {"status": "CLARIFY", "missing_inputs": missing}

        if spec.calculation_type == "income_gap":
            goal = Decimal(str(spec.provided_inputs["target_income"]))
            expected = Decimal(str(spec.provided_inputs["expected_income"]))
            value = max(Decimal("0"), goal - expected)
            return self._result(
                spec,
                "income_gap_v1",
                "math-v1",
                {"target_income": str(goal), "expected_income": str(expected)},
                value,
            )

        rule = self.policies.get(spec.policy_year)
        if rule is None:
            return {"status": "UNSUPPORTED_POLICY_VERSION", "policy_year": spec.policy_year}

        if spec.calculation_type == "tax_credit":
            if rule.contribution_limit is None or rule.pension_savings_limit is None:
                return {"status": "UNSUPPORTED_POLICY_VERSION", "policy_year": spec.policy_year}
            mode = str(spec.provided_inputs.get("mode") or "limit_summary")
            evidence = []
            if rule.evidence_source_key and rule.evidence_article_no:
                evidence.append({"source_key": rule.evidence_source_key, "article_no": rule.evidence_article_no})
            surcharge = rule.local_tax_surcharge_ratio or Decimal("0")

            def _effective(rate: Decimal | None) -> Decimal | None:
                if rate is None:
                    return None
                return (rate * (Decimal("1") + surcharge)).quantize(Decimal("0.0001"))

            # Official reference question: how much can be credited in total?
            if mode == "limit_summary" and "contribution_amount" not in spec.provided_inputs:
                std = _effective(rule.tax_credit_rate)
                low = _effective(rule.lower_income_tax_credit_rate)
                intermediate = {
                    "combined_credit_base_limit": str(rule.contribution_limit),
                    "pension_savings_credit_base_limit": str(rule.pension_savings_limit),
                    "standard_rate": str(rule.tax_credit_rate or ""),
                    "lower_income_rate": str(rule.lower_income_tax_credit_rate or ""),
                    "effective_standard_rate": str(std or ""),
                    "effective_lower_income_rate": str(low or ""),
                    "gross_salary_threshold": str(rule.gross_salary_threshold or ""),
                    "comprehensive_income_threshold": str(rule.comprehensive_income_threshold or ""),
                    "local_tax_surcharge_ratio": str(surcharge),
                }
                if rule.annual_contribution_limit is not None:
                    intermediate["annual_contribution_limit"] = str(rule.annual_contribution_limit)
                if rule.contribution_limit is not None and rule.pension_savings_limit is not None:
                    intermediate["irp_credit_remainder_limit"] = str(
                        rule.contribution_limit - rule.pension_savings_limit
                    )
                return self._result(
                    spec,
                    rule.formula_id,
                    rule.version,
                    intermediate,
                    rule.contribution_limit,
                    rule.source,
                    policy_evidence=evidence,
                )

            if mode == "isa_transfer":
                if rule.isa_extra_credit_base_limit is None or rule.isa_transfer_credit_ratio is None:
                    return {"status": "UNSUPPORTED_POLICY_VERSION", "policy_year": spec.policy_year}
                transfer = Decimal(str(spec.provided_inputs.get("isa_transfer_amount", 0)))
                if transfer < 0:
                    return {"status": "INVALID_INPUT", "field": "isa_transfer_amount"}
                ratio = rule.isa_transfer_credit_ratio
                cap = rule.isa_extra_credit_base_limit
                extra = min(transfer * ratio, cap).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                intermediate = {
                    "isa_transfer_amount": str(transfer),
                    "isa_transfer_credit_ratio": str(ratio),
                    "isa_extra_credit_base_limit": str(cap),
                    "isa_extra_credit_amount": str(extra),
                }
                return self._result(
                    spec,
                    rule.formula_id,
                    rule.version,
                    intermediate,
                    extra,
                    rule.source,
                    policy_evidence=evidence,
                )

            contribution = Decimal(str(spec.provided_inputs.get("contribution_amount", 0)))
            if contribution < 0:
                return {"status": "INVALID_INPUT", "field": "contribution_amount"}
            base = min(contribution, rule.contribution_limit)
            rate = rule.tax_credit_rate
            if rate is None:
                return {"status": "UNSUPPORTED_POLICY_VERSION", "policy_year": spec.policy_year}
            salary = spec.provided_inputs.get("gross_salary")
            comprehensive = spec.provided_inputs.get("comprehensive_income")
            band = "standard"
            if salary is not None and rule.gross_salary_threshold is not None and Decimal(str(salary)) <= rule.gross_salary_threshold:
                rate = rule.lower_income_tax_credit_rate or rate
                band = "lower_income"
            elif comprehensive is not None and rule.comprehensive_income_threshold is not None and Decimal(str(comprehensive)) <= rule.comprehensive_income_threshold:
                rate = rule.lower_income_tax_credit_rate or rate
                band = "lower_income"
            effective = _effective(rate) or rate
            credit = (base * effective).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            intermediate = {
                "credit_base": str(base),
                "national_rate": str(rate),
                "effective_rate": str(effective),
                "income_band": band,
                "combined_credit_base_limit": str(rule.contribution_limit),
                "local_tax_surcharge_ratio": str(surcharge),
                "contribution_amount": str(contribution),
            }
            if salary is not None:
                intermediate["gross_salary"] = str(salary)
            if spec.provided_inputs.get("premise_check") == "true":
                intermediate["premise_check"] = "true"
                if spec.provided_inputs.get("claimed_rate_percent"):
                    intermediate["claimed_rate_percent"] = str(spec.provided_inputs["claimed_rate_percent"])
                if spec.provided_inputs.get("claimed_credit_amount"):
                    intermediate["claimed_credit_amount"] = str(spec.provided_inputs["claimed_credit_amount"])
            return self._result(
                spec,
                rule.formula_id,
                rule.version,
                intermediate,
                credit,
                rule.source,
                policy_evidence=evidence,
            )

        contribution = Decimal(str(spec.provided_inputs["contribution_amount"]))
        if contribution < 0:
            return {"status": "INVALID_INPUT", "field": "contribution_amount"}
        if rule.contribution_limit is None:
            return {"status": "UNSUPPORTED_POLICY_VERSION", "policy_year": spec.policy_year}
        return self._result(
            spec,
            rule.formula_id,
            rule.version,
            {"contribution": str(contribution), "limit": str(rule.contribution_limit)},
            min(contribution, rule.contribution_limit),
            rule.source,
        )

    def _result(
        self,
        spec: CalculationQuerySpec,
        formula_id: str,
        version: str,
        intermediate: dict[str, str],
        value: Decimal,
        source: str = "verified_math",
        *,
        policy_evidence: list[dict[str, str]] | None = None,
    ) -> CalculationResult:
        rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return CalculationResult(
            calculation_type=spec.calculation_type,
            inputs={k: str(v) for k, v in spec.provided_inputs.items()},
            formula_id=formula_id,
            formula_version=version,
            policy_year=spec.policy_year,
            intermediate_values=intermediate,
            result=str(rounded),
            unit=spec.currency,
            rounding_applied=spec.rounding_policy,
            limitations=[],
            assumptions=[],
            policy_evidence=policy_evidence or [],
        )
