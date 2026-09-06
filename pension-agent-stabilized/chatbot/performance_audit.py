"""Performance value provenance audit. Never invent or rescale numbers."""
from __future__ import annotations

from typing import Any, Iterable


PERFORMANCE_VALUE_STATUS = (
    "VERIFIED",
    "SCALE_MISMATCH",
    "UNIT_MISSING",
    "SOURCE_CONFLICT",
    "UNVERIFIED",
)

FUND_CODE_MARKERS = ("펀드코드", "집합투자기구 명칭", "종류형 명칭")
PERCENT_UNITS = {"PERCENT", "PERCENT_PER_YEAR", "PERCENT_PER_MONTH"}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _evidence_texts(item: dict[str, Any], record: dict[str, Any] | None = None) -> list[str]:
    texts: list[str] = []
    wanted = set(item.get("evidence_ids") or [])
    for evidence in (record or {}).get("evidence") or []:
        if wanted and evidence.get("evidence_id") not in wanted:
            continue
        text = evidence.get("source_text") or evidence.get("text") or ""
        if text:
            texts.append(str(text))
    if item.get("source_text"):
        texts.append(str(item["source_text"]))
    return texts


def _value_in_text(value: float, text: str) -> bool:
    raw = str(int(value)) if float(value).is_integer() else str(value)
    compact = text.replace(",", "").replace(" ", "")
    return raw in compact or f"{value}" in compact


def _looks_like_fund_code_table(texts: Iterable[str]) -> bool:
    blob = "\n".join(texts)
    return any(marker in blob for marker in FUND_CODE_MARKERS)


def audit_performance_item(
    item: dict[str, Any],
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare a stored performance row to its attached source text.

    No arithmetic conversion is applied. A large PERCENT value is not treated
    as a return unless the source text also presents it as a return.
    """
    value = _as_float(item.get("value"))
    unit = str(item.get("unit") or "").upper() or None
    metric = item.get("metric_type") or item.get("metric")
    period = item.get("period")
    texts = _evidence_texts(item, record)
    source_blob = "\n".join(texts)
    json_value = value
    status = "UNVERIFIED"
    reason = "source_text_does_not_confirm_return_scale"
    if value is None:
        status, reason = "UNVERIFIED", "missing_numeric_value"
    elif not unit:
        status, reason = "UNIT_MISSING", "unit_missing"
    elif texts and _looks_like_fund_code_table(texts) and value is not None and _value_in_text(value, source_blob):
        status, reason = "SOURCE_CONFLICT", "value_matches_fund_code_table_not_return"
    elif unit in PERCENT_UNITS and value is not None and abs(value) > 100:
        if texts and _value_in_text(value, source_blob) and "수익률" not in source_blob and "%" not in source_blob:
            status, reason = "SCALE_MISMATCH", "large_percent_value_without_return_context"
        elif texts and "수익률" in source_blob and _value_in_text(value, source_blob):
            status, reason = "UNVERIFIED", "source_mentions_return_but_scale_not_confirmed"
        else:
            status, reason = "UNVERIFIED", "percent_magnitude_not_confirmed_by_source"
    elif unit in PERCENT_UNITS and value is not None and abs(value) <= 100:
        if texts and (_value_in_text(value, source_blob) or "수익률" in source_blob):
            status, reason = "VERIFIED", "value_and_percent_unit_consistent_with_source"
        else:
            status, reason = "UNVERIFIED", "reasonable_percent_range_but_source_not_matched"
    elif texts and value is not None and _value_in_text(value, source_blob):
        status, reason = "VERIFIED", "raw_value_matches_source_text"
    return {
        "raw_db_value": json_value,
        "display_value": None if status != "VERIFIED" else json_value,
        "metric_type": metric,
        "period": period,
        "unit": unit,
        "source_standard_json_value": json_value,
        "source_text_excerpt": source_blob[:240],
        "status": status,
        "reason": reason,
        "evidence_ids": list(item.get("evidence_ids") or []),
    }


def annotate_performance(record: dict[str, Any]) -> dict[str, Any]:
    annotated = []
    for item in record.get("performance") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["value_audit"] = audit_performance_item(item, record)
        annotated.append(row)
    record["performance"] = annotated
    return record


def selected_performance_audit(record: dict[str, Any], metric_type: str, period: str) -> dict[str, Any] | None:
    for item in record.get("performance") or []:
        item_metric = item.get("metric_type") or item.get("metric")
        if str(item_metric or "").casefold() != metric_type.casefold():
            continue
        if str(item.get("period") or "").upper() != period.upper():
            continue
        return item.get("value_audit") or audit_performance_item(item, record)
    return None
