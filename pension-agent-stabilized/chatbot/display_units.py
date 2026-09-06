"""User-facing formatters for canonical financial units.

Raw DB values and units are never mutated. This layer is display-only.
"""
from __future__ import annotations

from typing import Any


CANONICAL_UNIT_LABELS = {
    "PERCENT_PER_YEAR": "연 %",
    "PERCENT_PER_MONTH": "월 %",
    "PERCENT": "%",
    "KRW": "원",
    "BASIS_POINT": "bp",
    "BP": "bp",
    "DECIMAL": "소수",
}

INTERNAL_UNIT_ENUMS = tuple(CANONICAL_UNIT_LABELS.keys())

PUBLIC_PRODUCT_SOURCE = "투자설명서 / 상품 DB 기준"
INTERNAL_SOURCE_LEAKS = (
    "PostgreSQL/Standard JSON",
    "상품 PostgreSQL",
    "구조화 레코드",
    "product_limit=",
    "적용한 기본값:",
)


def normalize_unit(unit: Any) -> str:
    return str(unit or "").strip().upper()


def display_unit_label(unit: Any) -> str | None:
    key = normalize_unit(unit)
    if not key:
        return None
    return CANONICAL_UNIT_LABELS.get(key)


def format_number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer() and abs(value) >= 100:
        return str(int(value))
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text


def format_financial_value(
    value: Any,
    unit: Any = None,
    metric_type: str | None = None,
    *,
    status: str | None = None,
) -> str:
    """Format a stored numeric value for users without converting scale."""
    if value is None:
        return "확인된 값 없음"
    if status in {"SCALE_MISMATCH", "SOURCE_CONFLICT", "UNVERIFIED", "UNIT_MISSING"}:
        return "수익률 값의 단위/스케일 확인이 필요합니다"
    unit_key = normalize_unit(unit)
    if not unit_key:
        if metric_type in {"fund_return", "return", "performance"}:
            return f"{format_number(value)} (단위 확인 필요)"
        return f"{format_number(value)} (단위 확인 필요)"
    if unit_key == "PERCENT_PER_YEAR":
        return f"연 {format_number(value)}%"
    if unit_key == "PERCENT_PER_MONTH":
        return f"월 {format_number(value)}%"
    if unit_key == "PERCENT":
        return f"{format_number(value)}%"
    if unit_key in {"KRW", "WON"}:
        return f"{format_number(value)}원"
    if unit_key in {"BASIS_POINT", "BP"}:
        return f"{format_number(value)}bp"
    if unit_key == "DECIMAL":
        return f"{format_number(value)} (소수, 단위 확인 필요)"
    return f"{format_number(value)} (단위 확인 필요)"


def public_source_citation(source_file: str | None = None) -> str:
    if source_file:
        return f"{PUBLIC_PRODUCT_SOURCE} ({source_file})"
    return PUBLIC_PRODUCT_SOURCE


def strip_internal_presentation(text: str) -> str:
    """Remove debug / internal enum leaks from a user-facing answer."""
    cleaned = text or ""
    for leak in INTERNAL_SOURCE_LEAKS:
        cleaned = cleaned.replace(leak, "")
    for enum in INTERNAL_UNIT_ENUMS:
        cleaned = cleaned.replace(enum, "")
    cleaned = cleaned.replace("근거 출처:", PUBLIC_PRODUCT_SOURCE)
    lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
    return "\n".join(lines).strip()
