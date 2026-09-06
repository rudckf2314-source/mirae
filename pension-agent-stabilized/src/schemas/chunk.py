from enum import StrEnum

from pydantic import BaseModel, Field


class SectionType(StrEnum):
    PRODUCT_INFO = "PRODUCT_INFO"
    RISK_GRADE = "RISK_GRADE"
    INVESTMENT_OBJECTIVE = "INVESTMENT_OBJECTIVE"
    INVESTMENT_STRATEGY = "INVESTMENT_STRATEGY"
    INVESTMENT_RISK = "INVESTMENT_RISK"
    CLASS_INFO = "CLASS_INFO"
    FEES = "FEES"
    PERFORMANCE = "PERFORMANCE"
    AUM = "AUM"
    OTHER = "OTHER"


SECTION_SLUG: dict[SectionType, str] = {
    SectionType.PRODUCT_INFO: "product",
    SectionType.RISK_GRADE: "risk",
    SectionType.INVESTMENT_OBJECTIVE: "objective",
    SectionType.INVESTMENT_STRATEGY: "strategy",
    SectionType.INVESTMENT_RISK: "risks",
    SectionType.CLASS_INFO: "class",
    SectionType.FEES: "fees",
    SectionType.PERFORMANCE: "performance",
    SectionType.AUM: "aum",
    SectionType.OTHER: "other",
}


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    page_start: int
    page_end: int
    section_type: SectionType
    text: str
    page_source_text: str | None = None
    table_id: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class SectionSpan(BaseModel):
    section_type: SectionType
    page_start: int
    page_end: int
    heading: str | None = None
    score: float = 0.0
    keywords_hit: list[str] = Field(default_factory=list)
    text: str | None = None
