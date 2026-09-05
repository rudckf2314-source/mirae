from __future__ import annotations

import re

from schemas.chunk import SectionType
from schemas.document import DetectedTable
from parsers.table_parser import is_semantic_risk_table

NORMAL = "NORMAL"
SUSPICIOUS = "SUSPICIOUS"
BROKEN = "BROKEN"

DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def compact(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def is_date_cell(text: str | None) -> bool:
    raw = (text or "").strip().replace(".", "-").replace("/", "-")
    return bool(DATE_RE.match(raw))


def is_class_like(text: str | None) -> bool:
    blob = compact(text)
    if not blob or blob in {"종류", "클래스종류", "투자비용", "투자신탁"}:
        return False
    if "비교지수" in blob or "변동성" in blob:
        return True
    if re.search(r"\([A-Za-z][A-Za-z0-9\-]*\)", blob) and any(
        token in blob for token in ("수수료", "오프라인", "온라인", "종류")
    ):
        return True
    return blob.startswith("종류") and len(blob) <= 16


def assess_table(table: DetectedTable) -> str:
    if not table.rows:
        return BROKEN
    first_cells = [(row[0] if row else "") for row in table.rows]
    if table.section_type == SectionType.PERFORMANCE:
        date_first = sum(1 for cell in first_cells if is_date_cell(cell))
        labeled = sum(1 for cell in first_cells if is_class_like(cell))
        if date_first and labeled == 0:
            return BROKEN
        if date_first:
            return SUSPICIOUS
        if labeled == 0:
            return SUSPICIOUS
        return NORMAL
    if table.section_type == SectionType.FEES:
        labeled = sum(1 for cell in first_cells if is_class_like(cell))
        truncated = sum(1 for cell in first_cells if compact(cell).endswith("-") and "수수료" in compact(cell))
        jammed = sum(1 for row in table.rows for cell in row if len(re.findall(r"\d+(?:\.\d+)?%", cell or "")) >= 2)
        if labeled == 0:
            return BROKEN
        if truncated or jammed:
            return SUSPICIOUS
        header_len = len(table.headers)
        if header_len and any(abs(len(row) - header_len) > 3 for row in table.rows):
            return SUSPICIOUS
        return NORMAL
    if table.section_type == SectionType.INVESTMENT_RISK:
        if not is_semantic_risk_table(table):
            return BROKEN
        valid_rows = sum(
            1
            for row in table.rows
            if len(row) >= 2 and row[0].strip() and row[1].strip()
        )
        return NORMAL if valid_rows else BROKEN
    return SUSPICIOUS if table.rows else BROKEN


def heading_flags(text: str) -> tuple[bool, bool]:
    blob = compact(text)
    has_fee = "투자비용" in blob or ("판매수수료" in blob and "총보수" in blob)
    has_perf = "투자실적" in blob or "연평균수익률" in blob
    return has_fee, has_perf


def risk_heading_flag(text: str) -> bool:
    blob = compact(text)
    return (
        "투자위험" in blob
        and "주요내용" in blob
        and ("구분" in blob or "세부구분" in blob)
    )


def page_needs_fallback(text: str, tables: list[DetectedTable]) -> bool:
    needs = needed_sections(text, tables)
    return bool(needs)


def needed_sections(text: str, tables: list[DetectedTable]) -> set[SectionType]:
    has_fee_heading, has_perf_heading = heading_flags(text)
    needed: set[SectionType] = set()
    if has_fee_heading and not _has_normal(tables, SectionType.FEES):
        needed.add(SectionType.FEES)
    if has_perf_heading and not _has_normal(tables, SectionType.PERFORMANCE):
        needed.add(SectionType.PERFORMANCE)
    if risk_heading_flag(text) and not _has_normal(tables, SectionType.INVESTMENT_RISK):
        needed.add(SectionType.INVESTMENT_RISK)
    return needed


def _has_normal(tables: list[DetectedTable], section: SectionType) -> bool:
    return any(
        table.section_type == section and assess_table(table) == NORMAL and table.rows
        for table in tables
    )
