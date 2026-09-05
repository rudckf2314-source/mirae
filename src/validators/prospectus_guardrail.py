from __future__ import annotations

import re

import pymupdf

from exceptions import ProspectusRejectedError

COVER_MARKERS = (
    "투자설명서",
    "투 자 설 명 서",
    "집합투자증권",
    "집합투자기구",
)
SECTION_MARKERS = (
    "투자목적",
    "투자전략",
    "투자위험",
    "위험등급",
    "총보수",
    "보수 및 수수료",
    "종류 집합투자증권",
    "작성기준일",
    "효력발생일",
)
GUIDE_ONLY_MARKERS = (
    "퇴직연금이 무엇인가요",
    "확정급여형(DB",
    "개인형 퇴직연금제도(IRP)란",
)

_SPACE = re.compile(r"\s+")


def _fold(text: str) -> str:
    return _SPACE.sub("", text.casefold())


def extract_preview_text(pdf_bytes: bytes, max_pages: int = 10) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = []
        for index in range(min(doc.page_count, max_pages)):
            pages.append(doc[index].get_text() or "")
        return "\n".join(pages)
    finally:
        doc.close()


def inspect_investment_prospectus(pdf_bytes: bytes) -> dict[str, object]:
    text = extract_preview_text(pdf_bytes)
    folded = _fold(text)
    found_cover = [marker for marker in COVER_MARKERS if _fold(marker) in folded]
    found_sections = [marker for marker in SECTION_MARKERS if _fold(marker) in folded]
    guide_hits = [marker for marker in GUIDE_ONLY_MARKERS if _fold(marker) in folded]
    missing: list[str] = []
    if len(found_cover) < 1:
        missing.append("표지/명칭(투자설명서·집합투자기구)")
    if len(found_sections) < 2:
        missing.append("본문 섹션(투자목적·투자위험·위험등급·보수 등)")
    if guide_hits and not found_cover:
        missing.append("연금 안내문/FAQ로 보이며 투자설명서가 아님")
    accepted = not missing
    return {
        "accepted": accepted,
        "missing": missing,
        "found_cover": found_cover,
        "found_sections": found_sections,
        "guide_hits": guide_hits,
    }


def assert_investment_prospectus(pdf_bytes: bytes, file_name: str) -> dict[str, object]:
    result = inspect_investment_prospectus(pdf_bytes)
    if not result["accepted"]:
        raise ProspectusRejectedError(file_name, list(result["missing"]))
    return result
