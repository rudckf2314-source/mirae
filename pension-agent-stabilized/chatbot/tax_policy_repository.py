from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .legal_store import LegalStore


@dataclass(frozen=True)
class PensionTaxCreditPolicy:
    policy_year: int
    combined_credit_base_limit: Decimal
    pension_savings_credit_base_limit: Decimal
    standard_rate: Decimal
    lower_income_rate: Decimal
    gross_salary_threshold: Decimal
    comprehensive_income_threshold: Decimal
    isa_extra_credit_base_limit: Decimal
    isa_transfer_credit_ratio: Decimal
    annual_contribution_limit: Decimal | None
    local_tax_surcharge_ratio: Decimal
    formula_id: str
    version: str
    evidence_source_key: str
    evidence_article_no: str
    source_type: str


class TaxPolicyRepository:
    def __init__(self, store: LegalStore | None = None) -> None:
        self.store = store or LegalStore()

    def pension_tax_credit(self, policy_year: int) -> PensionTaxCreditPolicy | None:
        raw = self.store.get_policy_rule("PENSION_TAX_CREDIT", policy_year)
        if not raw:
            return None
        p: dict[str, Any] = raw["payload"]
        return PensionTaxCreditPolicy(
            policy_year=policy_year,
            combined_credit_base_limit=Decimal(str(p["combined_credit_base_limit"])),
            pension_savings_credit_base_limit=Decimal(str(p["pension_savings_credit_base_limit"])),
            standard_rate=Decimal(str(p["standard_rate"])),
            lower_income_rate=Decimal(str(p["lower_income_rate"])),
            gross_salary_threshold=Decimal(str(p["gross_salary_threshold"])),
            comprehensive_income_threshold=Decimal(str(p["comprehensive_income_threshold"])),
            isa_extra_credit_base_limit=Decimal(str(p.get("isa_extra_credit_base_limit", 0))),
            isa_transfer_credit_ratio=Decimal(str(p.get("isa_transfer_credit_ratio", 0))),
            annual_contribution_limit=(
                Decimal(str(p["annual_contribution_limit"]))
                if p.get("annual_contribution_limit") is not None
                else None
            ),
            local_tax_surcharge_ratio=Decimal(str(p.get("local_tax_surcharge_ratio", "0.10"))),
            formula_id=raw["formula_id"],
            version=raw["version"],
            evidence_source_key=raw["evidence_source_key"],
            evidence_article_no=raw["evidence_article_no"],
            source_type=raw["source_type"],
        )
