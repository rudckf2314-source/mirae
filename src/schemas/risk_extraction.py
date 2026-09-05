from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RiskStructureType(StrEnum):
    TABLE_2COL = "TABLE_2COL"
    TABLE_MULTI_COL = "TABLE_MULTI_COL"
    VERTICAL_PAIR = "VERTICAL_PAIR"
    HEADING_PARAGRAPH = "HEADING_PARAGRAPH"
    BULLET = "BULLET"
    INLINE = "INLINE"
    UNKNOWN = "UNKNOWN"


class RiskSemanticRole(StrEnum):
    RISK_NAME = "RISK_NAME"
    RISK_DESCRIPTION = "RISK_DESCRIPTION"
    RISK_CAUSE = "RISK_CAUSE"
    RISK_IMPACT = "RISK_IMPACT"
    RISK_MITIGATION = "RISK_MITIGATION"
    RISK_CATEGORY = "RISK_CATEGORY"
    SECTION_HEADING = "SECTION_HEADING"
    TABLE_HEADER = "TABLE_HEADER"
    OTHER = "OTHER"


class RiskSourceSpan(BaseModel):
    source_id: str
    page_number: int = Field(ge=1)
    raw_text: str
    normalized_text: str
    semantic_role: RiskSemanticRole = RiskSemanticRole.OTHER
    evidence_refs: list[str] = Field(default_factory=list)
    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    column_name: str | None = None
    bbox: tuple[float, float, float, float] | None = None


class RiskRegion(BaseModel):
    region_id: str
    document_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    raw_text: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    raw_headers: list[list[str]] = Field(default_factory=list)
    raw_rows: list[list[list[str]]] = Field(default_factory=list)
    raw_blocks: list[RiskSourceSpan] = Field(default_factory=list)


class AssembledRiskRecord(BaseModel):
    candidate_id: str
    name: str
    description: str
    structure_type: RiskStructureType
    evidence_refs: list[str] = Field(default_factory=list)
    name_span: RiskSourceSpan
    description_spans: list[RiskSourceSpan] = Field(default_factory=list)


class UnknownRiskTemplateDiagnostic(BaseModel):
    code: str = "UNKNOWN_RISK_TEMPLATE"
    document_id: str
    page: int = Field(ge=1)
    structure_type: RiskStructureType = RiskStructureType.UNKNOWN
    failure_stage: str
    reason: str
    raw_text: str = ""
    raw_headers: list[str] = Field(default_factory=list)
    raw_table: list[list[str]] = Field(default_factory=list)
    raw_blocks: list[RiskSourceSpan] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
