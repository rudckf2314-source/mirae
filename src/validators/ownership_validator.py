from __future__ import annotations

import re

from processing.class_candidates import is_plausible_class_name
from schemas.product import (
    CandidateOutcome,
    CanonicalProduct,
    OwnershipOutcome,
    TextWithEvidence,
)


class OwnershipValidator:
    """Enforce field-owner invariants before source verification."""

    def validate(self, product: CanonicalProduct) -> CanonicalProduct:
        warnings = product.extraction.warnings
        status = {
            (item.owner, item.field): item.status
            for item in product.extraction.ownership
        }

        if status.get(("table", "fees")) not in {None, "VALID"} and product.fees:
            product.fees = []
            warnings.append("ownership_invariant: non-VALID fee owner populated fees")
        if (
            status.get(("table", "performance")) not in {None, "VALID"}
            and product.performance
        ):
            product.performance = []
            warnings.append(
                "ownership_invariant: non-VALID performance owner populated performance"
            )

        valid_fees = []
        for fee in product.fees:
            if not is_plausible_class_name(fee.class_name) and re.sub(
                r"\s+", "", fee.class_name or ""
            ) != "투자신탁":
                warnings.append(
                    f"ownership_invariant: unresolved fee class code: {fee.class_name}"
                )
                continue
            valid_fees.append(fee)
        product.fees = valid_fees

        objective = _compact(product.product.investment_objective.text)
        strategy = _compact(product.product.investment_strategy.text)
        if objective and strategy and objective == strategy:
            strategy_refs = list(product.product.investment_strategy.evidence_refs)
            product.product.investment_strategy = TextWithEvidence()
            warnings.append("DUPLICATE_NARRATIVE: investment_strategy cleared")
            product.extraction.candidate_outcomes.append(
                CandidateOutcome(
                    field="investment_strategy",
                    owner="narrative",
                    candidate_id="narrative:investment_strategy:duplicate",
                    status="REJECTED",
                    reason="Same normalized text as investment_objective.",
                    evidence_refs=strategy_refs,
                )
            )
            self._replace_outcome(
                product,
                OwnershipOutcome(
                    field="investment_strategy",
                    owner="narrative",
                    status="REJECTED",
                    reason="Same normalized text as investment_objective.",
                ),
            )

        code = (product.product.fund_code or "").strip()
        if re.fullmatch(r"KR\d{8,}", code, re.I):
            product.product.fund_code = None
            warnings.append("ownership_invariant: document identifier rejected as fund_code")

        for index, risk in enumerate(product.product.investment_risks):
            if risk.name and not (risk.description or "").strip():
                warnings.append(f"risk_description_missing: investment_risks[{index}]")

        product.extraction.warnings = list(dict.fromkeys(warnings))
        return product

    @staticmethod
    def _replace_outcome(
        product: CanonicalProduct,
        outcome: OwnershipOutcome,
    ) -> None:
        product.extraction.ownership = [
            item
            for item in product.extraction.ownership
            if not (item.field == outcome.field and item.owner == outcome.owner)
        ]
        product.extraction.ownership.append(outcome)


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")
