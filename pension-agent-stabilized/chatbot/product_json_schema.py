"""Pydantic contracts for the immutable product-source JSON and its derived index."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RawModel(BaseModel):
    """Known fields are typed; source-schema additions remain readable."""

    model_config = ConfigDict(extra="allow")


class SourceDocument(RawModel):
    filename: str | None = None
    document_type: str | None = None
    as_of_date: str | None = None
    effective_date: str | None = None
    page_count: int | None = None
    file_hash: str | None = None


class Product(RawModel):
    official_name: str | None = None
    kofia_fund_code: str | None = None
    manager_name: str | None = None
    legal_form: str | None = None
    asset_type: str | None = None
    inception_date: str | None = None
    is_high_complexity_product: bool | None = None


class ProductClass(RawModel):
    class_key: str | None = None
    class_name: str | None = None
    kofia_fund_code: str | None = None
    pension_type: str | None = None
    eligibility_text: str | None = None
    channel: str | None = None
    is_online: bool | None = None
    inception_date: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Fee(RawModel):
    class_key: str | None = None
    fee_type: str | None = None
    rate: float | None = None
    unit: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ProductJsonDocument(RawModel):
    schema_version: str
    source_document: SourceDocument
    product: Product
    classes: list[ProductClass]
    fees: list[Fee] = Field(default_factory=list)
    risk_ratings: list[dict[str, Any]] = Field(default_factory=list)
    performance: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class PensionTypeCode(str, Enum):
    RETIREMENT_PENSION = "RETIREMENT_PENSION"
    PERSONAL_PENSION = "PERSONAL_PENSION"
    PENSION_SAVINGS = "PENSION_SAVINGS"
    INSTITUTIONAL = "INSTITUTIONAL"
    EMPLOYEE_WELFARE_PENSION = "EMPLOYEE_WELFARE_PENSION"
    WRAP = "WRAP"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class RawPensionTypeContext(BaseModel):
    """The minimum, non-secret context permitted for a normalizer decision."""

    pension_type_raw: str | None
    source_json_file: str
    source_filename: str | None = None
    class_key: str | None = None
    class_name: str | None = None
    eligibility_text: str | None = None
    schema_version: str
    evidence_ids: list[str] = Field(default_factory=list)
    product_name: str | None = None


class LLMNormalizationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pension_type_codes: list[PensionTypeCode] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1000)
    requires_review: bool


class PensionTypeNormalization(BaseModel):
    pension_type_codes: list[PensionTypeCode] = Field(min_length=1)
    pension_type_labels_ko: list[str] = Field(default_factory=list)
    normalization_method: Literal["python_rule", "llm", "unknown", "missing"]
    confidence: float = Field(ge=0, le=1)
    reason: str
    requires_review: bool
    normalizer_model: str | None = None
    normalizer_prompt_version: str | None = None

