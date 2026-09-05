from pydantic import BaseModel, Field

from .chunk import SectionType


class LayoutBlock(BaseModel):
    block_id: str
    text: str
    bbox: tuple[float, float, float, float]


class PageText(BaseModel):
    page_number: int
    text: str
    blocks: list[LayoutBlock] = Field(default_factory=list)


class DetectedTable(BaseModel):
    table_id: str
    page_number: int
    section_type: SectionType = SectionType.OTHER
    headers: list[str] = Field(default_factory=list)
    raw_headers: list[str] = Field(default_factory=list)
    column_roles: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    raw_row_count: int = 0
    extraction_method: str = "pymupdf"


class ParsedDocument(BaseModel):
    document_id: str
    document_hash: str
    file_name: str
    page_count: int
    pages: list[PageText] = Field(default_factory=list)
    tables: list[DetectedTable] = Field(default_factory=list)
