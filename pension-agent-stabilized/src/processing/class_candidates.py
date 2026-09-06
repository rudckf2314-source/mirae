from __future__ import annotations

import re

from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable

KIND_CLASS_RE = re.compile(r"종류[A-Za-z][A-Za-z0-9\-]*")
FEE_CLASS_RE = re.compile(
    r"수수료(?:선취|후취|미징구)"
    r"(?:-(?:오프라인|온라인슈퍼|온라인|직판|퇴직연금|개인연금|기관|랩,금전신탁|랩|무권유저비용|보수체감))*"
    r"\([A-Za-z][A-Za-z0-9\-]*\)"
)
CLASS_CODE_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9\-]*)\)$")
KIND_CODE_RE = re.compile(r"^종류([A-Za-z][A-Za-z0-9\-]*)$")
PREFIX_CLASS_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9\-]*)\((수수료(?:선취|후취|미징구).+)\)$"
)
TABLE_SOURCE_TYPES = {SectionType.FEES, SectionType.PERFORMANCE}
SKIP_NAMES = {
    "비교지수",
    "수익률변동성",
    "종류",
    "클래스종류",
    "투자비용",
    "투자신탁",
    "투자실적추이",
}


def normalize_class_name(name: str | None) -> str | None:
    if not name:
        return None
    text = re.sub(r"[\s\u3000]+", "", name).replace("ㆍ", "·").strip()
    if not text:
        return None
    prefix_match = PREFIX_CLASS_RE.fullmatch(text)
    if prefix_match:
        description = prefix_match.group(2).replace("–", "-")
        return f"{description}({prefix_match.group(1)})"
    fee_match = FEE_CLASS_RE.search(text)
    if fee_match:
        return fee_match.group(0)
    kind_match = KIND_CLASS_RE.search(text)
    if kind_match and text.startswith("종류"):
        return kind_match.group(0)
    return text


def class_code(name: str | None) -> str | None:
    text = normalize_class_name(name)
    if not text:
        return None
    match = CLASS_CODE_RE.search(text)
    if match:
        return match.group(1)
    match = KIND_CODE_RE.fullmatch(text)
    if match:
        return match.group(1)
    return None


def short_class_alias(name: str | None) -> str | None:
    code = class_code(name)
    return f"종류{code}" if code else None


def class_identity(name: str | None) -> str:
    text = normalize_class_name(name) or ""
    code = class_code(text)
    return code.lower() if code else text


def prefer_class_name(current: str | None, incoming: str | None) -> str | None:
    left = normalize_class_name(current)
    right = normalize_class_name(incoming)
    if not left:
        return right
    if not right:
        return left
    if "수수료" in right and "수수료" not in left:
        return right
    if len(right) > len(left) and class_identity(left) == class_identity(right):
        return right
    return left


def is_plausible_class_name(name: str | None) -> bool:
    text = normalize_class_name(name)
    if not text or text in SKIP_NAMES:
        return False
    if KIND_CLASS_RE.fullmatch(text):
        code = class_code(text) or ""
        if code.endswith("-"):
            return False
        return True
    if FEE_CLASS_RE.fullmatch(text):
        return True
    return False


def class_tokens_from_text(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (KIND_CLASS_RE, FEE_CLASS_RE):
        for match in pattern.finditer(re.sub(r"\s+", "", text or "")):
            name = normalize_class_name(match.group(0))
            if is_plausible_class_name(name):
                found.append(name)
    return found


def harvest_class_candidates(
    chunks: list[Chunk] | None = None,
    tables: list[DetectedTable] | None = None,
) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    summary_pages = {
        section: _summary_pages(tables, chunks, section) for section in TABLE_SOURCE_TYPES
    }

    def add(raw: str | None, on_summary: bool) -> None:
        tokens = class_tokens_from_text(raw or "")
        name = normalize_class_name(raw)
        if is_plausible_class_name(name) and name not in tokens:
            tokens.append(name)
        for token in tokens:
            if token in seen:
                continue
            if not on_summary and not _is_hyphenated_kind_c(token):
                continue
            seen.add(token)
            found.append(token)

    for chunk in chunks or []:
        if chunk.section_type not in TABLE_SOURCE_TYPES:
            continue
        pages = summary_pages.get(chunk.section_type)
        on_summary = pages is None or chunk.page_start in pages or chunk.page_end in pages
        for row in chunk.rows or []:
            if row:
                add(row[0], on_summary)
        if chunk.section_type in {SectionType.FEES, SectionType.PERFORMANCE}:
            for line in (chunk.text or "").splitlines():
                if _is_class_list_line(line):
                    add(line, on_summary)

    for table in tables or []:
        if table.section_type not in TABLE_SOURCE_TYPES:
            continue
        if table.extraction_method not in {
            "pymupdf_normalized",
            "text_fallback",
            "pdfplumber",
            "pdfminer_coordinate_fallback",
        }:
            continue
        pages = summary_pages.get(table.section_type)
        on_summary = pages is None or table.page_number in pages
        for row in table.rows or []:
            if row:
                add(row[0], on_summary)
    return found


def _summary_pages(
    tables: list[DetectedTable] | None,
    chunks: list[Chunk] | None,
    section: SectionType,
) -> set[int] | None:
    pages = sorted(
        {
            table.page_number
            for table in tables or []
            if table.section_type == section and table.rows
        }
    )
    if not pages:
        pages = sorted(
            {chunk.page_start for chunk in chunks or [] if chunk.section_type == section}
        )
    if not pages:
        return None
    start = pages[0]
    return {start, start + 1}


def _is_hyphenated_kind_c(name: str | None) -> bool:
    text = normalize_class_name(name)
    if not text or not text.startswith("종류C"):
        return False
    code = class_code(text) or ""
    return "-" in code


def _is_class_list_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line or "")
    if not compact:
        return False
    stripped = KIND_CLASS_RE.sub("", compact)
    stripped = FEE_CLASS_RE.sub("", stripped)
    stripped = re.sub(r"[-,./()]", "", stripped)
    return len(stripped) <= 6 and bool(KIND_CLASS_RE.search(compact) or FEE_CLASS_RE.search(compact))
