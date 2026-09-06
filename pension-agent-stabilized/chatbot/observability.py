"""Evaluation-only turn metadata. Never shown in the public answer."""
from __future__ import annotations

from typing import Any


GOLD_METADATA_KEYS = (
    "test_id",
    "session_id",
    "original_query",
    "resolved_query",
    "detected_intent",
    "actual_route",
    "retrieval_queries",
    "required_evidence",
    "tools_called",
    "source_types",
    "product_ids",
    "product_class_ids",
    "candidate_count",
    "candidate_ids",
    "selected_product_id",
    "db_row_count",
    "retrieval_top_k",
    "evidence_coverage",
    "claim_grounding",
    "raw_value",
    "display_value",
    "raw_unit",
    "normalized_unit",
    "normalization_status",
    "response_status",
    "latency_ms",
)


def build_gold_turn_metadata(
    *,
    question: str,
    envelope: dict[str, Any],
    state: dict[str, Any] | None = None,
    adapter: Any = None,
    session: dict[str, Any] | None = None,
    latency_ms: float | None = None,
    test_id: str | None = None,
) -> dict[str, Any]:
    meta = envelope.get("metadata") or {}
    state = state or {}
    products = list(state.get("product_results") or envelope.get("product_results") or [])
    trace = getattr(adapter, "last_search_trace", None) or state.get("product_execution_trace") or {}
    selected = (session or {}).get("selected_product") or (products[0] if products else {})
    first = products[0] if products else {}
    raw_unit = first.get("total_fee_unit") or first.get("selected_performance_unit")
    raw_value = first.get("total_fee")
    if first.get("selected_performance_value") is not None:
        raw_value = first.get("selected_performance_value")
        raw_unit = first.get("selected_performance_unit")
    audit = first.get("selected_performance_audit") or {}
    return {
        "test_id": test_id,
        "session_id": (session or {}).get("session_id"),
        "original_query": question,
        "resolved_query": state.get("normalized_question") or question,
        "detected_intent": meta.get("detected_intent") or state.get("route"),
        "actual_route": meta.get("route") or state.get("route"),
        "retrieval_queries": state.get("retrieval_queries") or [],
        "required_evidence": (state.get("spec_bundle") or {}).get("verification_spec"),
        "tools_called": list(state.get("tools") or meta.get("used_tools") or []),
        "source_types": list(meta.get("source_types") or []),
        "product_ids": [str(item.get("record_id") or "") for item in products],
        "product_class_ids": [str(item.get("class_key") or "") for item in products],
        "candidate_count": len(products),
        "candidate_ids": [str(item.get("record_id") or "") for item in products],
        "selected_product_id": (selected or {}).get("record_id"),
        "db_row_count": trace.get("db_rows_raw_count"),
        "retrieval_top_k": state.get("top_k"),
        "evidence_coverage": (envelope.get("evidence_summary") or {}),
        "claim_grounding": state.get("claim_grounding_report"),
        "raw_value": raw_value,
        "display_value": audit.get("display_value"),
        "raw_unit": raw_unit,
        "normalized_unit": audit.get("unit") or raw_unit,
        "normalization_status": audit.get("status") or first.get("selected_performance_status"),
        "response_status": envelope.get("status"),
        "latency_ms": latency_ms,
        "query_spec": trace.get("product_query_spec") or meta.get("query_spec"),
        "ranking_breakdown": [item.get("ranking_breakdown") for item in products if item.get("ranking_breakdown")],
    }
