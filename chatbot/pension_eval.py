"""Small deterministic Phase 6 golden-case loader and evaluator."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any


def load_golden_cases(path: str | Path) -> list[dict[str, Any]]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"case_id", "question", "expected_route", "expected_action", "expected_workers", "expected_verdict", "expected_llm_call_range", "required_evidence_domains", "required_source_types", "expected_constraints", "forbidden_behaviors"}
    if not isinstance(cases, list) or any(not required.issubset(item) for item in cases):
        raise ValueError("invalid_golden_cases")
    return cases


def evaluate(agent: Any, cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for case in cases:
        started = time.perf_counter()
        result = agent.answer(case["question"])
        meta = result.get("langgraph", {})
        route_ok = result.get("route") == case["expected_route"]
        action_ok = meta.get("ambiguity_action", "EXECUTE") == case["expected_action"]
        verdict_ok = meta.get("verification_verdict") == case["expected_verdict"] or case["expected_verdict"] == "ANY"
        llm = meta.get("llm_call_count", 0)
        lo, hi = case["expected_llm_call_range"]
        evidence_domains = set((meta.get("evidence_count_by_domain") or {}).keys())
        evidence_ok = set(case["required_evidence_domains"]).issubset(evidence_domains)
        calculation_ok = True
        if case["expected_route"] == "calculation" and "result" in case["expected_constraints"]:
            calculation_ok = str((result.get("calculation_result") or {}).get("result")) == str(case["expected_constraints"]["result"])
        llm_ok = lo <= llm <= hi
        rows.append({"case_id": case["case_id"], "passed": route_ok and action_ok and verdict_ok and llm_ok and evidence_ok and calculation_ok, "latency_ms": round((time.perf_counter() - started) * 1000, 3), "route_ok": route_ok, "action_ok": action_ok, "verdict_ok": verdict_ok, "evidence_ok": evidence_ok, "calculation_ok": calculation_ok, "llm_ok": llm_ok, "llm_call_count": llm})
    count = len(rows)
    def rate(key: str) -> float:
        return sum(bool(row[key]) for row in rows) / count if count else 0.0
    return {"case_count": count, "calculation_case_count": sum(case["expected_route"] == "calculation" for case in cases), "passed": sum(row["passed"] for row in rows), "pass_rate": rate("passed"), "route_accuracy": rate("route_ok"), "action_accuracy": rate("action_ok"), "verdict_accuracy": rate("verdict_ok"), "calculation_result_accuracy": rate("calculation_ok"), "evidence_requirement_rate": rate("evidence_ok"), "llm_call_range_rate": rate("llm_ok"), "rows": rows}


def critic_recommendation(report: dict[str, Any]) -> str:
    failures = report["case_count"] - report["passed"]
    return "CRITIC_NOT_NEEDED" if failures == 0 else "CRITIC_DEFERRED"
