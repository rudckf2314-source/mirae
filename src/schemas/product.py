import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .chunk import SectionType
from .risk_extraction import UnknownRiskTemplateDiagnostic
from .versions import CANONICAL_SCHEMA_VERSION, CanonicalSchemaVersion


def _parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


class RiskInfo(BaseModel):
    grade: int | None = None
    label: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("grade", mode="before")
    @classmethod
    def parse_grade(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        match = re.search(r"(\d+)", str(value))
        return int(match.group(1)) if match else None


class TextWithEvidence(BaseModel):
    text: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class InvestmentRiskItem(BaseModel):
    name: str | None = None
    description: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name_whitespace(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"\s+", " ", str(value)).strip(" |-·ㆍ,，;；:：")
        return normalized or None


class ProductInfo(BaseModel):
    name: str | None = None
    manager: str | None = None
    asset_type: str | None = None
    fund_code: str | None = None
    classification: list[str] = Field(default_factory=list)
    risk: RiskInfo = Field(default_factory=RiskInfo)
    investment_objective: TextWithEvidence = Field(default_factory=TextWithEvidence)
    investment_strategy: TextWithEvidence = Field(default_factory=TextWithEvidence)
    investment_risks: list[InvestmentRiskItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def drop_empty_risks(self):
        self.investment_risks = [
            item
            for item in self.investment_risks
            if (item.name and item.name.strip()) or (item.description and item.description.strip())
        ]
        return self


class ProductClass(BaseModel):
    class_name: str | None = None
    description: str | None = None
    inception_date: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class FeeItem(BaseModel):
    class_name: str | None = None
    fee_type: str | None = None
    rate: float | None = None
    unit: str | None = "%"
    as_of_date: str | None = None
    condition: str | None = None
    note: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    row_index: int | None = None
    column_name: str | None = None
    raw_cell_text: str | None = None

    @field_validator("rate", mode="before")
    @classmethod
    def parse_rate(cls, value: Any) -> float | None:
        return _parse_optional_float(value)

    @model_validator(mode="after")
    def copy_note_to_condition(self):
        if not self.condition and self.note:
            self.condition = self.note
        return self

    @field_validator("fee_type")
    @classmethod
    def normalize_fee_type(cls, value: str | None) -> str | None:
        if not value:
            return value
        aliases = {
            "sales_fee_rate": "sales_remuneration",
            "판매보수": "sales_remuneration",
            "synthetic_total_fee": "peer_group_total_fee",
            "동종유형 총보수": "peer_group_total_fee",
            "동종유형총보수": "peer_group_total_fee",
            "total_fee_cost": "total_fee_and_expenses",
            "총보수비용": "total_fee_and_expenses",
            "총보수·비용": "total_fee_and_expenses",
            "판매수수료": "sales_fee",
            "총보수": "total_fee",
        }
        return aliases.get(value, value)


class PerformanceItem(BaseModel):
    class_name: str | None = None
    subject: str | None = None
    metric_type: str | None = None
    period: str | None = None
    return_rate: float | None = None
    unit: str | None = "%"
    period_start: str | None = None
    period_end: str | None = None
    as_of_date: str | None = None
    kind: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    row_index: int | None = None
    column_name: str | None = None
    raw_cell_text: str | None = None

    @field_validator("return_rate", mode="before")
    @classmethod
    def parse_return_rate(cls, value: Any) -> float | None:
        return _parse_optional_float(value)

    @field_validator("period")
    @classmethod
    def normalize_period(cls, value: str | None) -> str | None:
        if not value:
            return value
        aliases = {
            "inception": "SINCE_INCEPTION",
            "설정일이후": "SINCE_INCEPTION",
            "설정일 이후": "SINCE_INCEPTION",
            "최근 1년": "1Y",
            "최근 2년": "2Y",
            "최근 3년": "3Y",
            "최근 5년": "5Y",
        }
        return aliases.get(value, value)

    @field_validator("metric_type")
    @classmethod
    def normalize_metric_type(cls, value: str | None) -> str | None:
        if not value:
            return value
        aliases = {
            "class": "fund_return",
            "fund": "fund_return",
            "benchmark": "benchmark_return",
            "비교지수": "benchmark_return",
            "volatility": "volatility",
            "수익률 변동성": "volatility",
        }
        return aliases.get(value, value)

    @model_validator(mode="after")
    def fill_subject(self):
        if not self.subject:
            self.subject = self.class_name
        if not self.metric_type:
            subject = (self.subject or "") + (self.kind or "")
            if "비교지수" in subject or self.kind == "benchmark":
                self.metric_type = "benchmark_return"
            elif "변동성" in subject or self.kind == "volatility":
                self.metric_type = "volatility"
            elif self.class_name or self.kind == "class":
                self.metric_type = "fund_return"
        return self


class AumItem(BaseModel):
    value: float | None = None
    currency: str | None = "KRW"
    unit: str | None = None
    as_of_date: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    chunk_id: str
    document_id: str
    file_name: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section_type: SectionType
    source_text: str = Field(min_length=1)
    table_markdown: str | None = None
    source_hash: str | None = None
    table_id: str | None = None
    row_index: int | None = None
    column_name: str | None = None

    @model_validator(mode="after")
    def validate_page_range(self):
        if self.page_end < self.page_start:
            raise ValueError("page_end cannot be before page_start")
        return self


class DocumentMeta(BaseModel):
    document_id: str
    document_hash: str
    file_name: str
    document_type: str = "investment_prospectus"
    as_of_date: str | None = None
    effective_date: str | None = None
    page_count: int | None = None


class ValidationReport(BaseModel):
    schema_status: str = "PASS"
    evidence_status: str = "PASS"
    completeness_status: str = "PASS"
    consistency_status: str = "PASS"


class VerificationItem(BaseModel):
    field_path: str
    status: str
    verdict: str | None = None
    method: str
    extracted_value: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str | None = None


class VerificationReport(BaseModel):
    status: str = "PASS"
    checked: int = 0
    pass_count: int = 0
    warning_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    items: list[VerificationItem] = Field(default_factory=list)


class OwnershipOutcome(BaseModel):
    field: str
    owner: str
    status: str
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class CandidateOutcome(BaseModel):
    field: str
    owner: str
    candidate_id: str
    status: str
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class NodeRunMetric(BaseModel):
    node: str
    duration_ms: float = 0.0
    cache_hit: bool = False
    llm_calls: int = 0
    executed: bool = True


class ExtractionRunReport(BaseModel):
    document_hash: str | None = None
    fingerprint_version: str | None = None
    canonical_fingerprint: str | None = None
    total_duration_ms: float = 0.0
    cache_hits: int = 0
    llm_calls: int = 0
    nodes: list[NodeRunMetric] = Field(default_factory=list)


class ExtractionMeta(BaseModel):
    status: str = "success"
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    info: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)
    ownership: list[OwnershipOutcome] = Field(default_factory=list)
    candidate_outcomes: list[CandidateOutcome] = Field(default_factory=list)
    validation: ValidationReport = Field(default_factory=ValidationReport)
    verification: VerificationReport = Field(default_factory=VerificationReport)
    run_report: ExtractionRunReport = Field(default_factory=ExtractionRunReport)
    risk_diagnostics: list[UnknownRiskTemplateDiagnostic] = Field(default_factory=list)


class CanonicalProduct(BaseModel):
    schema_version: CanonicalSchemaVersion = CANONICAL_SCHEMA_VERSION
    document: DocumentMeta
    product: ProductInfo = Field(default_factory=ProductInfo)
    classes: list[ProductClass] = Field(default_factory=list)
    fees: list[FeeItem] = Field(default_factory=list)
    performance: list[PerformanceItem] = Field(default_factory=list)
    aum: list[AumItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    extraction: ExtractionMeta = Field(default_factory=ExtractionMeta)

    @model_validator(mode="after")
    def validate_evidence_identity(self):
        evidence_ids = [item.chunk_id for item in self.evidence]
        duplicates = sorted({item for item in evidence_ids if evidence_ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate evidence chunk_id: {', '.join(duplicates)}")

        for item in self.evidence:
            if item.document_id != self.document.document_id:
                raise ValueError(
                    f"evidence {item.chunk_id} document_id does not match document.document_id"
                )
            if item.file_name != self.document.file_name:
                raise ValueError(
                    f"evidence {item.chunk_id} file_name does not match document.file_name"
                )
            if self.document.page_count is not None and item.page_end > self.document.page_count:
                raise ValueError(
                    f"evidence {item.chunk_id} page range exceeds document.page_count"
                )
        return self

    def all_evidence_refs(self) -> list[str]:
        refs: list[str] = []
        refs.extend(self.product.risk.evidence_refs)
        refs.extend(self.product.investment_objective.evidence_refs)
        refs.extend(self.product.investment_strategy.evidence_refs)
        for item in self.product.investment_risks:
            refs.extend(item.evidence_refs)
        for item in self.classes:
            refs.extend(item.evidence_refs)
        for item in self.fees:
            refs.extend(item.evidence_refs)
        for item in self.performance:
            refs.extend(item.evidence_refs)
        for item in self.aum:
            refs.extend(item.evidence_refs)
        # Metadata facts keep their provenance in owner/candidate outcomes.
        # These references are part of the canonical audit trail even when the
        # value model itself has no evidence_refs property.
        for item in self.extraction.ownership:
            refs.extend(item.evidence_refs)
        for item in self.extraction.candidate_outcomes:
            refs.extend(item.evidence_refs)
        return refs

    def to_summary(self) -> dict[str, Any]:
        risk = self.product.risk
        return {
            "document_id": self.document.document_id,
            "file_name": self.document.file_name,
            "product_name": self.product.name,
            "manager": self.product.manager,
            "asset_type": self.product.asset_type,
            "risk_grade": risk.grade,
            "risk_label": risk.label,
            "class_count": len(self.classes),
            "fee_count": len(self.fees),
            "performance_count": len(self.performance),
            "evidence_count": len(self.evidence),
            "status": self.extraction.status,
        }
