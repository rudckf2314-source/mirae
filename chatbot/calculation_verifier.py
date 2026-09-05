"""Independent deterministic verification for calculation-worker output."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .calculation_worker import CalculationResult
from .pension_evidence import Evidence
from .pension_verifier import VerificationCheck, VerificationReport


class CalculationRuleVerifier:
    """Recomputes supported calculations; it never trusts a worker verdict."""

    def verify(self, result: CalculationResult, evidence: list[Evidence]) -> VerificationReport:
        checks: list[VerificationCheck] = []

        def add(check_id: str, status: str, expected: object, actual: object, message: str) -> None:
            checks.append(VerificationCheck(
                check_id=check_id, rule=check_id, status=status,  # type: ignore[arg-type]
                expected=expected, actual=actual, message=message,
                evidence_ids=[item.evidence_id for item in evidence],
            ))

        if result.calculation_type == "income_gap":
            required = {"target_income", "expected_income"}
            add("calculation_required_inputs", "PASS" if required <= set(result.inputs) else "FAIL", sorted(required), sorted(result.inputs), "Required calculation inputs were checked.")
            try:
                target = Decimal(result.inputs["target_income"])
                expected = Decimal(result.inputs["expected_income"])
                parsed_result = Decimal(result.result)
            except (KeyError, InvalidOperation):
                target = expected = parsed_result = None
            non_negative = target is not None and expected is not None and target >= 0 and expected >= 0
            add("calculation_non_negative", "PASS" if non_negative else "FAIL", ">= 0", {"target_income": str(target), "expected_income": str(expected)}, "Negative amounts are not permitted.")
            valid_formula = result.formula_id == "income_gap_v1" and result.formula_version == "math-v1"
            add("calculation_formula", "PASS" if valid_formula else "FAIL", "income_gap_v1/math-v1", f"{result.formula_id}/{result.formula_version}", "Supported formula identity and version were checked.")
            recomputed = max(Decimal("0"), target - expected).quantize(Decimal("1"), rounding=ROUND_HALF_UP) if non_negative else None
            add("calculation_recompute", "PASS" if recomputed is not None and parsed_result == recomputed else "FAIL", str(recomputed), str(parsed_result), "The result was independently recomputed.")
        elif result.calculation_type == "tax_credit":
            try:
                parsed_result = Decimal(result.result)
                combined_limit = Decimal(result.intermediate_values["combined_credit_base_limit"])
            except (KeyError, InvalidOperation):
                parsed_result = combined_limit = None
            limit_mode = result.inputs.get("mode") == "limit_summary"
            if limit_mode:
                add("tax_credit_limit_result", "PASS" if parsed_result is not None and parsed_result == combined_limit else "FAIL", str(combined_limit), str(parsed_result), "Tax credit base limit was recomputed from policy.")
                try:
                    pension_limit = Decimal(result.intermediate_values["pension_savings_credit_base_limit"])
                except (KeyError, InvalidOperation):
                    pension_limit = None
                add("tax_credit_sublimit", "PASS" if pension_limit is not None and combined_limit is not None and pension_limit <= combined_limit else "FAIL", "pension savings <= combined", str(pension_limit), "Pension-savings sublimit was checked.")
            else:
                try:
                    base = Decimal(result.intermediate_values["credit_base"])
                    rate = Decimal(result.intermediate_values["rate"])
                    recomputed = (base * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                except (KeyError, InvalidOperation):
                    recomputed = None
                add("tax_credit_recompute", "PASS" if recomputed is not None and parsed_result == recomputed else "FAIL", str(recomputed), str(parsed_result), "Tax credit amount was recomputed from policy.")
            add("tax_credit_policy_identity", "PASS" if result.formula_id.startswith("pension_tax_credit_") else "FAIL", "pension_tax_credit_*", result.formula_id, "Policy formula identity was checked.")
            legal_evidence = [item for item in evidence if item.domain == "law" and item.status == "matched"]
            calc_evidence = [item for item in evidence if item.domain == "calculation" and item.status == "matched"]
            add("tax_credit_legal_evidence", "PASS" if legal_evidence else "FAIL", ">=1 matched law evidence", len(legal_evidence), "Policy law evidence was checked.")
            add("tax_credit_calculation_evidence", "PASS" if calc_evidence else "FAIL", ">=1 matched calculation evidence", len(calc_evidence), "Calculation evidence was checked.")
        else:
            add("calculation_type_supported", "FAIL", "supported type", result.calculation_type, "Calculation type is not supported by the verifier.")

        add("calculation_unit", "PASS" if result.unit == "KRW" else "FAIL", "KRW", result.unit, "Calculation unit was checked.")
        add("calculation_rounding", "PASS" if result.rounding_applied == "KRW_HALF_UP" else "FAIL", "KRW_HALF_UP", result.rounding_applied, "Rounding policy was checked.")

        failures = [check.check_id for check in checks if check.status == "FAIL"]
        domain_counts: dict[str, int] = {}
        for domain in ("calculation", "law"):
            count = len([item for item in evidence if item.domain == domain and item.status == "matched"])
            if count:
                domain_counts[domain] = count
        return VerificationReport(
            verdict="FAIL" if failures else "PASS",
            checks=checks,
            failures=failures,
            warnings=[],
            evidence_count_by_domain=domain_counts,
            evidence_count_by_status={"matched": sum(domain_counts.values())} if domain_counts else {},
        )
