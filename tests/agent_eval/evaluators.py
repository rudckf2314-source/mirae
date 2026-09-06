"""Deterministic scoring for live agent envelopes and LangGraph state."""

from __future__ import annotations

import re
from typing import Any

INTERNAL_LEAKS = (
    "source_versions",
    "document_evidence",
    "schema_status",
    "evidence_status",
    "Traceback",
    "stack trace",
    "요청을 완료하기 전에 근거 검증을 통과하지 못했습니다",
)

REASONING_LEAKS = (
    "We need to",
    "We have to",
    "The user asks",
    "Let's check",
    "From the evidence",
    "From doc",
    "Also mention",
    "structured DB evidence",
    "PDF evidence",
    "analysis:",
    "reasoning:",
    "thought:",
    "내부 메모",
    "사고 과정",
)

GENERIC_FUND_FRAGMENTS = {
    "투자신탁",
    "자투자신탁",
    "모투자신탁",
    "증권자투자신탁",
    "증권투자신탁",
}

UI_ROUTE_MISSING = "경로: -"

INTERNAL_UNIT_ENUMS = (
    "PERCENT_PER_YEAR",
    "PERCENT_PER_MONTH",
    "BASIS_POINT",
    "DECIMAL",
)

INTERNAL_DEBUG_LEAKS = (
    "product_limit=",
    "PostgreSQL/Standard JSON",
    "적용한 기본값:",
)

EXCLUDED_BY_TOLERANCE = {
    "moderate": {"VERY_AGGRESSIVE", "AGGRESSIVE"},
    "conservative": {"VERY_AGGRESSIVE", "AGGRESSIVE", "MODERATE_AGGRESSIVE"},
}

GRADE_TO_BUCKET = {
    1: "VERY_AGGRESSIVE",
    2: "AGGRESSIVE",
    3: "MODERATE_AGGRESSIVE",
    4: "MODERATE",
    5: "CONSERVATIVE",
    6: "VERY_CONSERVATIVE",
}

FUND_NAME_RE = re.compile(
    r"[가-힣A-Za-z0-9]+(?:증권)?(?:자)?투자신탁[^\s,.'\"”]{0,24}"
)


def map_intents(
    route: str,
    status: str,
    evidence_policy: str,
    question: str,
    session: dict[str, Any] | None = None,
) -> list[str]:
    q = question
    labels: list[str] = []
    if status == "clarify":
        labels.append("CLARIFY")
    if evidence_policy == "NOT_REQUIRED" or route == "conversation":
        if any(token in q for token in ("예시", "샘플")):
            labels.extend(["EXAMPLE_RESPONSE", "CLARIFY_EXAMPLE", "HYPOTHETICAL_EXAMPLE"])
        else:
            labels.append("CONVERSATION")
    if route == "document" or route.startswith("document"):
        if any(token in q for token in ("가입", "개설", "신청", "중도인출")):
            labels.append("ACCOUNT_PROCEDURE")
        else:
            labels.extend(["GENERAL_EDUCATION", "GENERAL_CONCEPT"])
    if "law" in (route or ""):
        labels.append("LEGAL_CONDITION")
    if route == "calculation" or (route or "").startswith("calculation"):
        labels.extend(["LEGAL_CONDITION", "GENERAL_EDUCATION"])
    if route == "product" or (route or "").startswith("product"):
        pending = ((session or {}).get("pending_task") or {}).get("intent")
        confirmed = (session or {}).get("confirmed_constraints") or {}
        if "추천" in q or pending == "PRODUCT_RECOMMENDATION" or (
            confirmed.get("risk_tolerance") and confirmed.get("investment_horizon")
        ):
            labels.append("PRODUCT_RECOMMENDATION")
        if "보수" in q or "수수료" in q:
            labels.extend(["PRODUCT_FEE_FILTER", "PRODUCT_FEE_SORT"])
        if "위험" in q:
            labels.extend(["PRODUCT_RISK_LOOKUP", "PRODUCT_RISK_FILTER"])
        if "수익률" in q:
            labels.append("PRODUCT_PERFORMANCE_LOOKUP")
        if not labels:
            labels.append("PRODUCT_LOOKUP")
    if "근거" in q:
        labels.append("EVIDENCE_REQUEST")
    if "없는 내용" in q or "자료에 없" in q:
        labels.append("SOURCE_COVERAGE")
    if "예시로" in q or "안정형 투자자" in q:
        labels.append("HYPOTHETICAL_EXAMPLE")
    return list(dict.fromkeys(labels))


def source_types_from(envelope: dict[str, Any], state: dict[str, Any] | None) -> list[str]:
    types: list[str] = []

    def _ingest_domain(domain: str) -> None:
        if domain == "document":
            types.append("enterprise_rag")
            types.append("enterprise_document")
        elif domain == "product":
            types.append("product")
            types.append("postgres")
        elif domain == "law":
            types.append("law")
            types.append("external_api")
        elif domain == "calculation":
            types.append("calculation")

    for item in envelope.get("sources") or []:
        _ingest_domain(str(item.get("domain") or ""))
    # ResponseGuard may keep domains on internal_sources when public labels are cleaned.
    for item in ((envelope.get("metadata") or {}).get("internal_sources") or []):
        _ingest_domain(str(item.get("domain") or ""))
    route = ((envelope.get("metadata") or {}).get("route") or "")
    if "product" in route:
        types.append("postgres")
    if state:
        if state.get("product_results"):
            types.extend(["product", "postgres"])
        if state.get("results"):
            types.extend(["enterprise_rag", "enterprise_document"])
        final_result = state.get("final_result") or {}
        if final_result.get("pdf_evidence") or state.get("pdf_evidence"):
            types.extend(["enterprise_rag", "enterprise_document"])
        if (state.get("law_result") or {}).get("success"):
            types.extend(["law", "external_api"])
    return list(dict.fromkeys(types))


def extract_fund_names(text: str) -> list[str]:
    return list(dict.fromkeys(FUND_NAME_RE.findall(text or "")))


def _norm_product_name(name: str) -> str:
    folded = re.sub(r"[\s*`_·\-.,\"'()]+", "", name or "")
    folded = re.sub(r"(의|은|는|을|를|만|도)+$", "", folded)
    return folded


def invented_products(answer: str, catalog: set[str]) -> list[str]:
    # Precision-only filter: do not lower scoring thresholds. Generic fund-family
    # nouns / particles / (주식|채권) class labels are not proprietary inventions.
    from chatbot.product_entity_precision import is_generic_financial_noun

    invented = []
    known_folded = [_norm_product_name(known) for known in catalog if known]
    for name in extract_fund_names(answer):
        folded = _norm_product_name(name)
        if not folded:
            continue
        if is_generic_financial_noun(name) or is_generic_financial_noun(folded):
            continue
        if folded in GENERIC_FUND_FRAGMENTS or re.sub(r"(에|의|은|는|을|를|에서|으로|로)$", "", folded) in GENERIC_FUND_FRAGMENTS:
            continue
        if any(folded in known or known in folded for known in known_folded):
            continue
        if any(known.replace("미래에셋", "") in folded for known in known_folded if len(known.replace("미래에셋", "")) >= 8):
            continue
        tokens = [token for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", folded) if token not in {"증권", "투자신탁", "자투자신탁", "모투자신탁", "펀드", "채권", "주식"}]
        if tokens and any(all(token in known for token in tokens) for known in known_folded):
            continue
        invented.append(name)
    return invented


def hard_fails(
    *,
    expect: dict[str, Any],
    question: str,
    envelope: dict[str, Any],
    trace: dict[str, Any],
    session_before: dict[str, Any] | None,
    session_after: dict[str, Any] | None,
    catalog: set[str],
    previous_products: list[str],
) -> list[str]:
    reasons: list[str] = []
    answer = str(envelope.get("answer") or "")
    route = str((envelope.get("metadata") or {}).get("route") or trace.get("actual_route") or "")
    status = str(envelope.get("status") or "")

    for leak in INTERNAL_LEAKS:
        if leak in answer:
            reasons.append(f"A:internal_string_exposed:{leak}")
    for leak in REASONING_LEAKS:
        if leak.lower() in answer.lower():
            reasons.append(f"A:reasoning_leakage:{leak}")
    if "Traceback" in answer or "File \"" in answer:
        reasons.append("A:stack_trace_exposed")
    if UI_ROUTE_MISSING in answer:
        reasons.append("B:ui_route_dash")
    if route and UI_ROUTE_MISSING in answer:
        reasons.append("B:route_exists_but_ui_dash")

    preferred = expect.get("preferred_sources") or []
    enterprise_needed = any(item.startswith("enterprise") for item in preferred)
    if enterprise_needed and expect.get("answer_required") and not expect.get("require_clarify"):
        if not trace.get("enterprise_rag_used") and status == "success":
            reasons.append("C:enterprise_source_not_used")

    if expect.get("require_postgres") and not trace.get("postgres_used"):
        reasons.append("D:postgres_not_used_for_product_fact")

    if expect.get("keep_session") and session_before:
        if not session_after or session_after.get("session_id") != session_before.get("session_id"):
            reasons.append("E:session_id_lost")

    if expect.get("evidence_fail_forbidden"):
        if status in {"safe_stop", "system_error"} or "근거 검증을 통과하지 못했습니다" in answer:
            reasons.append("F:example_stopped_by_evidence_fail")

    invented = invented_products(answer, catalog)
    if invented:
        reasons.append("G:invented_product:" + ",".join(invented[:3]))

    if expect.get("no_arbitrary_product") and trace.get("product_names"):
        reasons.append("G:arbitrary_product_selected")

    if expect.get("retain_candidate_set") and previous_products and trace.get("product_names"):
        overlap = set(previous_products) & set(trace["product_names"])
        if not overlap and len(trace["product_names"]) >= 1:
            reasons.append("E:candidate_set_reset")

    if expect.get("requested_result_required"):
        if status == "safe_stop" or int(trace.get("product_count") or 0) < int(expect.get("minimum_result_count") or 1):
            reasons.append("D:requested_product_result_missing")
        if expect.get("safe_stop_forbidden_if_matching_db_rows_exist") and status == "safe_stop":
            raw_hits = (trace.get("product_execution_trace") or {}).get("db_rows_after_filter_count")
            if raw_hits is None or int(raw_hits) >= 1:
                reasons.append("D:safe_stop_despite_matching_db_rows")

    if expect.get("forbidden_product_names"):
        forbidden = set(expect["forbidden_product_names"])
        if any(any(token in name for token in forbidden) for name in (trace.get("product_names") or [])):
            reasons.append("G:unrelated_product_substitute")

    if expect.get("answer_required") and not expect.get("require_clarify"):
        if status in {"safe_stop", "input_error", "error", "system_error"}:
            reasons.append("D:safe_stop_for_required_answer")

    if expect.get("must_return_product") or expect.get("safe_stop_forbidden"):
        if status == "safe_stop" or int(trace.get("product_count") or 0) < int(expect.get("minimum_products") or expect.get("minimum_result_count") or 1):
            reasons.append("D:recommendation_candidates_missing")

    if expect.get("must_use_previous_candidates"):
        if not (session_before or {}).get("last_candidates"):
            reasons.append("E:previous_candidates_required")
        if expect.get("must_return_selected_product") and not (session_after or {}).get("selected_product") and not trace.get("product_names"):
            reasons.append("E:selected_product_missing")

    if expect.get("must_resolve_selected_product") and not trace.get("pronoun_resolved") and not (session_after or {}).get("selected_product"):
        reasons.append("E:selected_product_unresolved")

    if expect.get("unrelated_substitute_forbidden") and any(item.startswith("G:unrelated_product_substitute") for item in reasons):
        reasons.append("G:unrelated_substitute_hard_fail")

    if expect.get("expected_variants"):
        names = " ".join(trace.get("product_names") or []) + " " + (envelope.get("answer") or "")
        missing = [variant for variant in expect["expected_variants"] if variant not in names]
        if missing:
            reasons.append("D:family_variants_missing:" + ",".join(missing))

    for enum in INTERNAL_UNIT_ENUMS:
        if enum in answer:
            reasons.append(f"A:internal_unit_leak:{enum}")
    for leak in INTERNAL_DEBUG_LEAKS:
        if leak in answer:
            reasons.append(f"A:internal_debug_leak:{leak}")

    if expect.get("must_answer_product_risk"):
        has_grade = bool(re.search(r"위험등급|투자위험등급|높은 위험|보통 위험|낮은 위험", answer))
        has_risk_evidence = bool(re.search(r"주요 위험|투자위험|시장위험|주식가격", answer))
        if not (has_grade or has_risk_evidence):
            reasons.append("D:product_risk_answer_missing")

    confirmed = (session_after or {}).get("confirmed_constraints") or {}
    tolerance = str(confirmed.get("risk_tolerance") or expect.get("risk_tolerance") or "")
    excluded = EXCLUDED_BY_TOLERANCE.get(tolerance)
    if excluded and (expect.get("must_return_product") or "추천" in question):
        grades = [item for item in (trace.get("product_risk_grades") or []) if item is not None]
        buckets = [GRADE_TO_BUCKET.get(int(grade)) for grade in grades if str(grade).isdigit() or isinstance(grade, int)]
        leaked = [bucket for bucket in buckets if bucket in excluded]
        if leaked:
            reasons.append("D:risk_mapping_excluded_bucket:" + ",".join(sorted(set(leaked))))

    if re.search(r"수익률\s+\d{4,}", answer) and "단위/스케일 확인" not in answer:
        reasons.append("D:performance_scale_anomaly_unhandled")

    return reasons


def score_turn(
    *,
    expect: dict[str, Any],
    question: str,
    envelope: dict[str, Any],
    trace: dict[str, Any],
    session_before: dict[str, Any] | None,
    session_after: dict[str, Any] | None,
    fails: list[str],
    llm_judge: dict[str, Any] | None,
) -> dict[str, Any]:
    route = str(trace.get("actual_route") or "")
    status = str(envelope.get("status") or "")
    answer = str(envelope.get("answer") or "").strip()
    intents = list(trace.get("detected_intent") or [])

    routing = 0
    preferred_routes = expect.get("preferred_routes") or []
    expected_intents = expect.get("intent") or []
    if expected_intents and any(item in intents for item in expected_intents):
        routing += 1
    if preferred_routes:
        if route in preferred_routes:
            routing += 1
        elif route:
            routing += 0
    elif route:
        routing += 1
    if expect.get("require_clarify") and status == "clarify":
        routing = max(routing, 2)
    if expect.get("postgres_must_not_be_primary") and route == "product":
        routing = min(routing, 1)

    context = 2
    if expect.get("keep_session"):
        context = 0
        if session_before and session_after and session_after.get("session_id") == session_before.get("session_id"):
            context += 1
        if expect.get("fill_slots"):
            confirmed = (session_after or {}).get("confirmed_constraints") or {}
            if all(confirmed.get(field) for field in expect["fill_slots"]):
                context += 1
            elif any(confirmed.get(field) for field in expect["fill_slots"]):
                context += 0
        elif session_after and (session_after.get("active_intent") or session_after.get("pending_question")):
            context += 1
        if expect.get("resolve_pronoun") and not trace.get("pronoun_resolved"):
            context = min(context, 1)
    elif not expect.get("keep_session"):
        context = 2

    source = 0
    actual_sources = set(trace.get("source_types") or [])
    preferred = set(expect.get("preferred_sources") or [])
    if not preferred:
        source = 2 if status in {"clarify", "success"} else 1
    else:
        if actual_sources & preferred:
            source += 1
        if preferred <= actual_sources or (expect.get("require_clarify") and status == "clarify"):
            source += 1
        if expect.get("require_postgres") and trace.get("postgres_used"):
            source = max(source, 1)
            if "product" in actual_sources:
                source = 2

    grounding = 2
    if expect.get("answer_required") and not answer:
        grounding = 0
    if any(item.startswith("G:") for item in fails):
        grounding = 0
    if expect.get("risk_grade_max") is not None:
        grades = [item for item in (trace.get("product_risk_grades") or []) if item is not None]
        if grades and any(int(grade) > int(expect["risk_grade_max"]) for grade in grades):
            grounding = 0
            fails.append("D:risk_grade_constraint_violated")
        elif grades:
            grounding = 2
        elif trace.get("postgres_used"):
            grounding = 1
    if expect.get("max_products") and trace.get("product_count"):
        if int(trace["product_count"]) > int(expect["max_products"]):
            grounding = min(grounding, 1)
            fails.append("D:product_limit_exceeded")
    if expect.get("fee_sort_asc") and trace.get("query_sort_by") != "total_fee":
        grounding = min(grounding, 1)
    if expect.get("performance_period") and trace.get("query_sort_by") not in {"performance", "fund_return"}:
        grounding = min(grounding, 1)
        fails.append("D:performance_query_not_observed")
    if expect.get("requested_result_required") and (
        status == "safe_stop" or int(trace.get("product_count") or 0) < int(expect.get("minimum_result_count") or 1)
    ):
        grounding = 0

    usefulness = 0
    if answer:
        usefulness += 1
    if status in {"success", "clarify"} and len(answer) >= 20:
        usefulness += 1
    if status in {"safe_stop", "system_error"}:
        usefulness = min(usefulness, 1)
    if expect.get("must_contain_any"):
        if any(token in answer for token in expect["must_contain_any"]):
            usefulness = max(usefulness, 1)
        else:
            usefulness = min(usefulness, 1)
    if llm_judge:
        judged = int(llm_judge.get("usefulness") or 0)
        usefulness = max(0, min(2, judged))

    for token in expect.get("must_not_contain") or []:
        if token in answer:
            fails.append(f"A:must_not_contain:{token}")

    scores = {
        "routing": max(0, min(2, routing)),
        "context_retention": max(0, min(2, context)),
        "source_selection": max(0, min(2, source)),
        "grounding": max(0, min(2, grounding)),
        "answer_usefulness": max(0, min(2, usefulness)),
    }
    total = sum(scores.values())
    hard = list(dict.fromkeys(fails))
    passed = (not hard) and total >= 8
    return {
        "scores": scores,
        "total": total,
        "hard_fails": hard,
        "pass_fail": "PASS" if passed else "FAIL",
        "failure_reasons": hard or ([] if passed else [f"score_below_8:{total}"]),
    }
