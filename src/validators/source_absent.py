"""Detect normal SOURCE_ABSENT / NOT_APPLICABLE cases without inventing values."""

from __future__ import annotations

import re

from schemas.chunk import Chunk
from schemas.document import DetectedTable

_ABSENT_EXPLICIT = (
    "신규설정으로해당사항없음",
    "신규펀드로서해당사항없음",
)
_PERF_CONTEXT = (
    "연평균수익률",
    "투자실적추이",
    "투자실적",
    "운용실적",
    "기간수익률",
)


def compact_text(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _performance_source_blob(
    chunks: list[Chunk] | None,
    tables: list[DetectedTable] | None,
) -> str:
    parts: list[str] = []
    for chunk in chunks or []:
        text = chunk.text or ""
        if any(token in text for token in (*_PERF_CONTEXT, "해당사항", "신규설정", "신규펀드")):
            parts.append(text)
    for table in tables or []:
        blob = " ".join(table.headers or [])
        blob += " " + " ".join(" ".join(row) for row in (table.rows or [])[:12])
        if any(token in blob for token in (*_PERF_CONTEXT, "해당사항", "신규설정", "신규펀드", "최근")):
            parts.append(blob)
    return compact_text("\n".join(parts))


def is_performance_source_absent(
    chunks: list[Chunk] | None = None,
    tables: list[DetectedTable] | None = None,
) -> bool:
    """True when the prospectus explicitly says performance is N/A (new fund etc.)."""
    blob = _performance_source_blob(chunks, tables)
    if not blob:
        return False
    if any(marker in blob for marker in _ABSENT_EXPLICIT):
        return True
    # Compact forms like "가.연평균수익률해당사항없음" / "나.연도별수익률추이해당사항없음".
    if re.search(r"(?:연평균수익률|연도별수익률추이|투자실적(?:추이)?)(?:\(세전기준\))?해당사항없음", blob):
        return True
    return False
