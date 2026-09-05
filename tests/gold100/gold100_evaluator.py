"""Gold-100 scoring — does not loosen T001–T022 evaluators."""

from __future__ import annotations

import re
from typing import Any

from tests.agent_eval.evaluators import (
    INTERNAL_DEBUG_LEAKS,
    INTERNAL_LEAKS,
    INTERNAL_UNIT_ENUMS,
    REASONING_LEAKS,
    invented_products,
)


def _norm_num(token: str) -> str:
    return token.replace(",", "").strip()


def number_present(answer: str, token: str) -> bool:
    raw = _norm_num(token)
    text = answer.replace(",", "")
    if not raw:
        return False
    # exact token or Korean man-won style for large integers
    if raw in text:
        return True
    try:
        value = float(raw)
    except ValueError:
        return False
    if value.is_integer() and value >= 10000:
        man = int(value) // 10000
        if man >= 1 and f"{man}만" in answer:
            return True
    if value.is_integer() and 100 <= value < 10000:
        # 1800만 already handled; 900만 etc.
        if f"{int(value)}만" in answer or f"{int(value)} 만" in answer:
            return True
    # percent forms
    if raw.endswith("%") or f"{raw}%" in answer or f"{raw} %" in answer:
        return True
    return False


def fact_coverage(expected_numbers: list[str], answer: str) -> dict[str, Any]:
    required = list(expected_numbers or [])
    hit = [n for n in required if number_present(answer, n)]
    miss = [n for n in required if n not in hit]
    ratio = (len(hit) / len(required)) if required else None
    return {
        "required_count": len(required),
        "hit_count": len(hit),
        "missed": miss,
        "coverage": ratio,
    }


def citation_coverage(answer: str, sources: list[dict[str, Any]], trace: dict[str, Any]) -> dict[str, Any]:
    has_source_objs = bool(sources)
    has_citation_text = any(
        token in (answer or "")
        for token in ("출처", "근거", "투자설명서", "쪽", ".pdf", "법령", "조")
    )
    enterprise = bool(trace.get("enterprise_rag_used"))
    postgres = bool(trace.get("postgres_used"))
    return {
        "has_source_objects": has_source_objs,
        "has_citation_text": has_citation_text,
        "enterprise_rag_used": enterprise,
        "postgres_used": postgres,
        "covered": has_source_objs or has_citation_text or enterprise or postgres,
    }


def classify_failure_codes(codes: list[str]) -> dict[str, int]:
    buckets = {
        "leak": 0,
        "routing": 0,
        "source": 0,
        "safe_stop": 0,
        "hallucination": 0,
        "fact_coverage": 0,
        "abstention": 0,
        "correction": 0,
        "product": 0,
        "legal_calc": 0,
        "other": 0,
    }
    for code in codes:
        if code.startswith("A:") or "leak" in code:
            buckets["leak"] += 1
        elif "route" in code:
            buckets["routing"] += 1
        elif "source" in code or "enterprise" in code or "postgres" in code:
            buckets["source"] += 1
        elif "safe_stop" in code:
            buckets["safe_stop"] += 1
        elif "invented" in code or "hallucin" in code or "unrelated" in code or "arbitrary" in code:
            buckets["hallucination"] += 1
        elif "fact" in code or "numeric" in code:
            buckets["fact_coverage"] += 1
        elif "abstention" in code or "clarify" in code:
            buckets["abstention"] += 1
        elif "correction" in code:
            buckets["correction"] += 1
        elif "product" in code:
            buckets["product"] += 1
        elif "calc" in code or "law" in code:
            buckets["legal_calc"] += 1
        else:
            buckets["other"] += 1
    return buckets


def evaluate_case(
    *,
    case: dict[str, Any],
    envelope: dict[str, Any],
    trace: dict[str, Any],
    catalog: set[str],
) -> dict[str, Any]:
    hints = case.get("adapter_eval_hints") or {}
    answer = str(envelope.get("answer") or "")
    status = str(envelope.get("status") or "")
    route = str(trace.get("actual_route") or (envelope.get("metadata") or {}).get("route") or "")
    sources = list(envelope.get("sources") or [])
    fails: list[str] = []

    # Hard leaks (always)
    for leak in INTERNAL_LEAKS:
        if leak in answer:
            fails.append(f"A:internal_string_exposed:{leak}")
    for leak in REASONING_LEAKS:
        if leak.lower() in answer.lower():
            fails.append(f"A:reasoning_leakage:{leak}")
    for enum in INTERNAL_UNIT_ENUMS:
        if enum in answer:
            fails.append(f"A:internal_unit_leak:{enum}")
    for leak in INTERNAL_DEBUG_LEAKS:
        if leak in answer:
            fails.append(f"A:internal_debug_leak:{leak}")
    if "Traceback" in answer or 'File "' in answer:
        fails.append("A:stack_trace_exposed")

    invented = invented_products(answer, catalog)
    if invented:
        fails.append("G:hallucination_invented_product:" + ",".join(invented[:3]))

    if hints.get("no_arbitrary_product") and trace.get("product_names"):
        fails.append("G:arbitrary_product_on_abstention")

    # Required answer vs safe_stop
    if hints.get("answer_required") and not hints.get("require_clarify"):
        if status in {"safe_stop", "input_error", "error", "system_error"}:
            fails.append("D:safe_stop_for_required_answer")
    if hints.get("safe_stop_forbidden") and status == "safe_stop":
        fails.append("D:safe_stop_forbidden")

    # Matching DB rows but safe_stop
    if status == "safe_stop":
        raw_hits = (trace.get("product_execution_trace") or {}).get("db_rows_after_filter_count")
        if raw_hits is not None and int(raw_hits) >= 1:
            fails.append("D:safe_stop_despite_matching_db_rows")

    # Abstention / clarify
    if hints.get("require_abstention_or_clarify"):
        clarify_ok = status == "clarify" or any(
            token in answer
            for token in ("추가 정보", "알려주세요", "위험 수준", "투자기간", "성향", "부족")
        )
        if not clarify_ok:
            fails.append("D:abstention_or_clarify_missing")
        if status == "success" and trace.get("product_names") and hints.get("no_arbitrary_product"):
            fails.append("G:recommended_product_despite_missing_info")

    # Correction of false premise
    if hints.get("require_correction"):
        if not any(token in answer for token in ("아니", "아닙니다", "아니라", "옳지", "잘못", "교정", "해당하지")):
            fails.append("D:correction_missing")

    # Routing accuracy against adapter hints (not Excel gold route column)
    route_families = hints.get("route_families") or []
    route_ok = True
    if route_families and route:
        route_ok = any(
            fam == route or fam in route or route.startswith(fam)
            for fam in route_families
        )
        if not route_ok and not hints.get("require_abstention_or_clarify"):
            fails.append(f"R:route_family_mismatch:actual={route}")

    # Source selection
    source_types = list(trace.get("source_types") or [])
    if hints.get("require_postgres") and not trace.get("postgres_used"):
        fails.append("C:postgres_not_used_for_product_fact")
    if hints.get("require_enterprise_document") and status == "success" and not hints.get("require_clarify"):
        if not (trace.get("enterprise_rag_used") or "enterprise_document" in source_types or "enterprise_rag" in source_types):
            fails.append("C:enterprise_source_not_used")
    if hints.get("require_calculation_route") and status in {"success", "clarify"}:
        calc_ok = "calculation" in route or "calculation" in source_types or any(
            re.search(rf"{re.escape(_norm_num(n))}", answer.replace(",", ""))
            for n in (case.get("expected_numbers_from_기대답변") or [])[:3]
        )
        # Still require route preference but allow numeric evidence soft path only for coverage metric
        if "calculation" not in route and "law" not in route and "document" not in route:
            fails.append(f"R:calculation_family_route_missing:actual={route}")

    # Fact / numeric coverage from Excel 기대 답변
    coverage = fact_coverage(case.get("expected_numbers_from_기대답변") or [], answer)
    if (
        coverage["required_count"] >= 2
        and status == "success"
        and not hints.get("require_abstention_or_clarify")
        and coverage["coverage"] is not None
        and coverage["coverage"] < 0.34
    ):
        fails.append(
            "D:required_fact_coverage_low:"
            f"{coverage['hit_count']}/{coverage['required_count']}"
        )

    # Calculation exactness: if calculation family and many numbers expected
    if hints.get("require_calculation_route") and status == "success" and coverage["required_count"] >= 1:
        if coverage["coverage"] is not None and coverage["coverage"] < 0.5:
            fails.append(
                "D:calculation_numeric_mismatch:"
                f"missed={','.join(coverage['missed'][:5])}"
            )

    cite = citation_coverage(answer, sources, trace)
    if (
        status == "success"
        and not hints.get("require_abstention_or_clarify")
        and not cite["covered"]
        and hints.get("answer_required")
    ):
        fails.append("F:citation_or_source_missing")

    empty_answer = status == "success" and not answer.strip()
    if empty_answer:
        fails.append("D:empty_success_answer")

    pass_fail = "FAIL" if fails else "PASS"

    return {
        "pass_fail": pass_fail,
        "failure_reasons": fails,
        "failure_buckets": classify_failure_codes(fails),
        "route_ok": route_ok,
        "fact_coverage": coverage,
        "citation": cite,
        "status": status,
        "route": route,
        "source_types": source_types,
        "adapter_hints_used": True,
    }
