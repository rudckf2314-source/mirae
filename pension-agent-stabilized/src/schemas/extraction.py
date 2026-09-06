from pydantic import BaseModel, Field

from .product import (
    AumItem,
    CandidateOutcome,
    FeeItem,
    OwnershipOutcome,
    PerformanceItem,
    ProductClass,
    ProductInfo,
    NodeRunMetric,
)
from .risk_extraction import UnknownRiskTemplateDiagnostic


class LLMExtractionResult(BaseModel):
    """LLM structured output. Document metadata and evidence objects are filled by backend."""

    as_of_date: str | None = None
    effective_date: str | None = None
    product: ProductInfo = Field(default_factory=ProductInfo)
    classes: list[ProductClass] = Field(default_factory=list)
    fees: list[FeeItem] = Field(default_factory=list)
    performance: list[PerformanceItem] = Field(default_factory=list)
    aum: list[AumItem] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ownership: list[OwnershipOutcome] = Field(default_factory=list)
    candidate_outcomes: list[CandidateOutcome] = Field(default_factory=list)
    run_metrics: list[NodeRunMetric] = Field(default_factory=list)
    risk_diagnostics: list[UnknownRiskTemplateDiagnostic] = Field(default_factory=list)


class RiskClassificationResult(BaseModel):
    """LLM output that can only select backend-generated candidate IDs."""

    accepted_candidate_ids: list[str] = Field(default_factory=list)
