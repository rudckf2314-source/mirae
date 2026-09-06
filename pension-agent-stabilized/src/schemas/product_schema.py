from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .versions import STANDARD_SCHEMA_VERSION, StandardSchemaVersion


# ---------------------------------------------------------------------------
# Common enums
# ---------------------------------------------------------------------------

class ExtractionStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"
    PARSE_FAILED = "PARSE_FAILED"


class DocumentType(str, Enum):
    INVESTMENT_PROSPECTUS = "INVESTMENT_PROSPECTUS"


class HedgingSubject(str, Enum):
    FEEDER_FUND = "feeder_fund"
    MASTER_FUND = "master_fund"
    CURRENT_FUND = "current_fund"
    OTHER_FUND = "other_fund"


class RateCondition(str, Enum):
    EXACT = "EXACT"
    MAX = "MAX"
    MIN = "MIN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class EvidenceExtractionMethod(str, Enum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    REGEX = "REGEX"
    LLM = "LLM"
    MANUAL = "MANUAL"


class NarrativeType(str, Enum):
    INVESTMENT_OBJECTIVE = "INVESTMENT_OBJECTIVE"
    INVESTMENT_STRATEGY = "INVESTMENT_STRATEGY"
    INVESTMENT_RISK = "INVESTMENT_RISK"
    SPECIFIC_RISK = "SPECIFIC_RISK"
    HEDGING_POLICY = "HEDGING_POLICY"
    REDEMPTION_RESTRICTION = "REDEMPTION_RESTRICTION"
    ELIGIBILITY = "ELIGIBILITY"
    TAX_CONSIDERATION = "TAX_CONSIDERATION"
    OTHER_MATERIAL_INFORMATION = "OTHER_MATERIAL_INFORMATION"


class TransactionType(str, Enum):
    PURCHASE = "PURCHASE"
    REDEMPTION = "REDEMPTION"
    CONVERSION = "CONVERSION"


# ---------------------------------------------------------------------------
# Source / evidence
# ---------------------------------------------------------------------------

class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    document_type: DocumentType = DocumentType.INVESTMENT_PROSPECTUS
    as_of_date: Optional[date] = None
    effective_date: Optional[date] = None
    revision_date: Optional[date] = None
    page_count: Optional[int] = Field(default=None, ge=1)
    file_hash: Optional[str] = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    page: int = Field(ge=1)
    section: Optional[str] = None
    source_text: str = Field(min_length=1)
    table_markdown: Optional[str] = None
    source_hash: Optional[str] = None
    row_index: Optional[int] = Field(default=None, ge=0)
    column_name: Optional[str] = None
    raw_cell_text: Optional[str] = None
    extraction_method: EvidenceExtractionMethod
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ExtractionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str
    issue_type: ExtractionStatus
    severity: str = "WARNING"
    message: str
    page: Optional[int] = Field(default=None, ge=1)


class QualityControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_status: str = "PASS"
    verification_fail_count: int = Field(default=0, ge=0)
    contradicted_fields: list[str] = Field(default_factory=list)
    review_required: bool = False


# ---------------------------------------------------------------------------
# Product / class
# ---------------------------------------------------------------------------

class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_key: str
    official_name: str
    kofia_fund_code: Optional[str] = None
    manager_name: Optional[str] = None

    # Keep normalized labels flexible in v0.1.
    # Later they can be migrated to controlled vocabulary tables.
    legal_form: Optional[str] = None
    asset_type: Optional[str] = None

    is_open_end: Optional[bool] = None
    is_additional: Optional[bool] = None
    is_class_type: Optional[bool] = None
    is_master_feeder: Optional[bool] = None
    is_convertible: Optional[bool] = None
    is_high_complexity_product: Optional[bool] = None

    inception_date: Optional[date] = None


class RiskRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: int = Field(
        ge=1,
        le=6,
        description="Korean fund risk scale: 1 is the highest risk and 6 is the lowest risk.",
    )
    label: Optional[str] = None
    method: Optional[str] = None
    as_of_date: Optional[date] = None
    evidence_ids: list[str] = Field(default_factory=list)


class ProductClass(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_key: str
    class_name: str
    kofia_fund_code: Optional[str] = None

    sales_charge_type: Optional[str] = None
    channel: Optional[str] = None
    pension_type: Optional[str] = None
    eligibility_text: Optional[str] = None

    is_online: Optional[bool] = None
    is_cdsc_class: Optional[bool] = None
    is_conversion_enabled: Optional[bool] = None

    inception_date: Optional[date] = None
    evidence_ids: list[str] = Field(default_factory=list)


class InvestmentProfile(BaseModel):
    """Source-grounded search facets; null means no verified PDF fact."""

    model_config = ConfigDict(extra="forbid")

    primary_asset: Optional[str] = None
    investment_regions: list[str] = Field(default_factory=list)
    investment_countries: list[str] = Field(default_factory=list)
    investment_sectors: list[str] = Field(default_factory=list)
    investment_styles: list[str] = Field(default_factory=list)
    benchmark_name: Optional[str] = None
    equity_ratio_min: Optional[float] = Field(default=None, ge=0, le=100)
    equity_ratio_max: Optional[float] = Field(default=None, ge=0, le=100)
    bond_ratio_min: Optional[float] = Field(default=None, ge=0, le=100)
    bond_ratio_max: Optional[float] = Field(default=None, ge=0, le=100)
    overseas_asset_ratio_min: Optional[float] = Field(default=None, ge=0, le=100)
    overseas_asset_ratio_max: Optional[float] = Field(default=None, ge=0, le=100)
    derivative_usage: Optional[bool] = None
    recommended_horizon: Optional[str] = None
    principal_loss_possible: Optional[bool] = None
    evidence_ids: list[str] = Field(default_factory=list)


class LiquidityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_key: Optional[str] = None
    transaction_type: TransactionType
    cutoff_time: Optional[str] = None
    pricing_day_offset: Optional[int] = Field(default=None, ge=0)
    payment_day_offset: Optional[int] = Field(default=None, ge=0)
    redemption_fee: Optional[float] = Field(default=None, ge=0)
    restriction_text: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Charges / fees
# ---------------------------------------------------------------------------

class SalesCharge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_key: str
    charge_type: str

    rate: Optional[float] = Field(default=None, ge=0)
    rate_min: Optional[float] = Field(default=None, ge=0)
    rate_max: Optional[float] = Field(default=None, ge=0)

    rate_unit: str = "PERCENT"
    rate_condition: RateCondition = RateCondition.UNKNOWN

    base_amount: Optional[str] = None
    timing: Optional[str] = None
    condition_text: Optional[str] = None

    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self):
        if (
            self.rate_min is not None
            and self.rate_max is not None
            and self.rate_min > self.rate_max
        ):
            raise ValueError("rate_min cannot be greater than rate_max")
        return self


class Fee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_key: str
    fee_type: str
    rate: Optional[float] = Field(default=None, ge=0)
    unit: str = "PERCENT_PER_YEAR"

    as_of_date: Optional[date] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None

    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_effective_dates(self):
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from > self.effective_to
        ):
            raise ValueError("effective_from cannot be after effective_to")
        return self


# ---------------------------------------------------------------------------
# Structural rules
# ---------------------------------------------------------------------------

class ClassTransitionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_class: str
    to_class: str
    automatic: bool

    trigger_type: Optional[str] = None
    minimum_holding_months: Optional[int] = Field(default=None, ge=0)
    condition_text: Optional[str] = None

    evidence_ids: list[str] = Field(default_factory=list)


class FundConversionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_class: Optional[str] = None
    target_product_name: str
    target_class: Optional[str] = None

    conversion_allowed: bool = True
    conversion_fee_rate: Optional[float] = Field(default=None, ge=0)
    conversion_count_limit: Optional[int] = Field(default=None, ge=0)
    condition_text: Optional[str] = None

    evidence_ids: list[str] = Field(default_factory=list)


class MasterFeederRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    master_product_name: str
    minimum_investment_ratio: Optional[float] = Field(default=None, ge=0, le=100)
    maximum_investment_ratio: Optional[float] = Field(default=None, ge=0, le=100)
    ratio_unit: str = "PERCENT"

    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_investment_ratio(self):
        if (
            self.minimum_investment_ratio is not None
            and self.maximum_investment_ratio is not None
            and self.minimum_investment_ratio > self.maximum_investment_ratio
        ):
            raise ValueError(
                "minimum_investment_ratio cannot exceed maximum_investment_ratio"
            )
        return self


class HedgingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: HedgingSubject
    fund_name: Optional[str] = None
    is_hedged: Optional[bool] = None

    hedge_ratio_min_pct: Optional[float] = Field(default=None, ge=0, le=100)
    hedge_ratio_max_pct: Optional[float] = Field(default=None, ge=0, le=100)

    hedge_from_currency: Optional[str] = None
    hedge_to_currency: Optional[str] = None
    residual_fx_exposure: Optional[str] = None

    policy_text: Optional[str] = None
    as_of_date: Optional[date] = None
    status: ExtractionStatus = ExtractionStatus.FOUND

    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_hedge_ratio(self):
        if (
            self.hedge_ratio_min_pct is not None
            and self.hedge_ratio_max_pct is not None
            and self.hedge_ratio_min_pct > self.hedge_ratio_max_pct
        ):
            raise ValueError(
                "hedge_ratio_min_pct cannot exceed hedge_ratio_max_pct"
            )

        # A fund explicitly marked as unhedged should not carry a hedge ratio.
        if self.is_hedged is False and (
            self.hedge_ratio_min_pct is not None
            or self.hedge_ratio_max_pct is not None
        ):
            raise ValueError(
                "An unhedged subject cannot have hedge ratio values"
            )
        return self


# ---------------------------------------------------------------------------
# Performance / flows / financial metrics
# ---------------------------------------------------------------------------

class PerformanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_key: Optional[str] = None

    metric: str
    period: str
    return_type: Optional[str] = None

    value: Optional[float] = None
    unit: str = "PERCENT"

    as_of_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None

    evidence_ids: list[str] = Field(default_factory=list)


class CapitalFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_key: Optional[str] = None

    period_start: date
    period_end: date

    opening_units: Optional[float] = None
    opening_amount: Optional[float] = None

    subscription_units: Optional[float] = None
    subscription_amount: Optional[float] = None

    redemption_units: Optional[float] = None
    redemption_amount: Optional[float] = None

    ending_units: Optional[float] = None
    ending_amount: Optional[float] = None

    unit_scale: Optional[str] = None
    units_scale: Optional[str] = None
    amount_scale: Optional[str] = None
    currency: str = "KRW"

    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_start > self.period_end:
            raise ValueError("period_start cannot be after period_end")
        return self


class FinancialMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_key: Optional[str] = None

    metric_type: str
    raw_value: Optional[float] = None
    raw_unit: Optional[str] = None

    normalized_value_krw: Optional[float] = None
    as_of_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None

    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Narrative / RAG bridge
# ---------------------------------------------------------------------------

class Narrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative_type: NarrativeType
    subject: str = "current_fund"
    text: str

    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Root Standard JSON contract
# ---------------------------------------------------------------------------

class ProductExtraction(BaseModel):
    """
    Standard JSON v0.1 contract between:
      PDF/parser/LLM extraction
               ↓
          Pydantic validation
               ↓
          deterministic DB loader
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: StandardSchemaVersion = STANDARD_SCHEMA_VERSION

    source_document: SourceDocument
    product: Product
    investment_profile: Optional[InvestmentProfile] = None

    risk_ratings: list[RiskRating] = Field(default_factory=list)
    classes: list[ProductClass] = Field(default_factory=list)

    sales_charges: list[SalesCharge] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list)

    class_transition_rules: list[ClassTransitionRule] = Field(default_factory=list)
    fund_conversion_rules: list[FundConversionRule] = Field(default_factory=list)
    master_feeder_relations: list[MasterFeederRelation] = Field(default_factory=list)
    hedging_policies: list[HedgingPolicy] = Field(default_factory=list)

    performance: list[PerformanceRecord] = Field(default_factory=list)
    capital_flows: list[CapitalFlow] = Field(default_factory=list)
    financial_metrics: list[FinancialMetric] = Field(default_factory=list)
    liquidity_rules: list[LiquidityRule] = Field(default_factory=list)

    narratives: list[Narrative] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    # Keys are logical JSON paths, e.g.:
    # "product.manager_name", "risk_ratings[0].grade"
    field_status: dict[str, ExtractionStatus] = Field(default_factory=dict)

    extraction_issues: list[ExtractionIssue] = Field(default_factory=list)
    quality_control: QualityControl = Field(default_factory=QualityControl)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self):
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence_id values must be unique")
        return self

    @model_validator(mode="after")
    def validate_class_references(self):
        class_keys = {item.class_key for item in self.classes}
        invalid: list[str] = []
        for field_name in ("sales_charges", "fees", "performance", "liquidity_rules"):
            for index, item in enumerate(getattr(self, field_name)):
                class_key = getattr(item, "class_key", None)
                if class_key is not None and class_key not in class_keys:
                    invalid.append(f"{field_name}[{index}].class_key={class_key}")
        if invalid:
            raise ValueError(
                "class_key referential integrity failed: " + ", ".join(invalid)
            )
        return self

    @model_validator(mode="after")
    def validate_evidence_references(self):
        known = {item.evidence_id for item in self.evidence}
        missing: list[str] = []
        groups = (
            "risk_ratings", "classes", "sales_charges", "fees",
            "class_transition_rules", "fund_conversion_rules",
            "master_feeder_relations", "hedging_policies", "performance",
            "capital_flows", "financial_metrics", "liquidity_rules", "narratives",
        )
        for group in groups:
            for index, item in enumerate(getattr(self, group)):
                for evidence_id in item.evidence_ids:
                    if evidence_id not in known:
                        missing.append(f"{group}[{index}]={evidence_id}")
        if self.investment_profile is not None:
            for evidence_id in self.investment_profile.evidence_ids:
                if evidence_id not in known:
                    missing.append(f"investment_profile={evidence_id}")
        if missing:
            raise ValueError("evidence referential integrity failed: " + ", ".join(missing))
        return self
