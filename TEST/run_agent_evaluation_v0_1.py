#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from pathlib import Path
from typing import Any

def load_cases(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def subset_ok(actual: list[str], required: list[str]) -> bool:
    return all(item in actual for item in required)

def deterministic_eval(repo_root: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    sys.path.insert(0, str(repo_root))
    from chatbot.query_router import QueryRouter, product_search_hints
    from chatbot.pension_ambiguity import AmbiguityGate
    from chatbot.calculation_gateway import classify

    hints: tuple[str, ...] = ()
    try:
        from chatbot.product_db_adapter import create_product_db_adapter

        adapter = create_product_db_adapter()
        hints = product_search_hints(getattr(adapter, "records", []) or [])
    except Exception:
        hints = ()
    router = QueryRouter(product_hints=hints)
    gate = AmbiguityGate()
    results = []

    for case in data["cases"]:
        expected = case["expected_orchestration"]
        decision = router.decide(case["question"])
        ambiguity = gate.decide(
            case["question"],
            decision.tools,
            named_product=router.mentions_named_product(case["question"]),
        )
        calc_type = classify(case["question"])

        checks = {}
        checks["route"] = decision.route in expected["route_any_of"]
        checks["tools"] = subset_ok(decision.tools, expected["tools_required"])
        if expected.get("ambiguity_action_any_of"):
            checks["ambiguity"] = ambiguity.action in expected["ambiguity_action_any_of"]
        if expected.get("calculation_type_any_of"):
            checks["calculation_type"] = calc_type in expected["calculation_type_any_of"]

        verdict = "PASS" if all(checks.values()) else "FAIL"
        results.append({
            "case_id": case["case_id"],
            "verdict": verdict,
            "checks": checks,
            "actual_route": decision.route,
            "actual_tools": decision.tools,
            "actual_ambiguity_action": ambiguity.action,
            "actual_missing_fields": ambiguity.missing_fields,
            "actual_calculation_type": calc_type,
            "known_current_gaps": case.get("known_current_gaps", []),
        })
    return results

def full_eval(repo_root: Path, data: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    sys.path.insert(0, str(repo_root))
    from chatbot.agent_core import PensionAgentCore
    from chatbot.pension_langgraph_agent import PensionLangGraphAgent

    try:
        agent = PensionLangGraphAgent(PensionAgentCore())
    except Exception as exc:
        raise RuntimeError(
            "PensionLangGraphAgent 초기화 실패. 프로젝트 의존성/환경변수/데이터 경로를 확인하세요: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    results = []
    for case in data["cases"]:
        expected = case["expected_orchestration"]
        try:
            state = agent.invoke(case["question"], top_k=top_k)
            final_result = state.get("final_result") or {}
            route = state.get("route") or final_result.get("route")
            tools = state.get("tools") or final_result.get("tools") or []
            ambiguity_action = (state.get("ambiguity_decision") or {}).get("action")
            calc_result = state.get("calculation_result") or final_result.get("calculation_result") or {}
            calc_type = calc_result.get("calculation_type")
            response = agent.response_guard.guard(final_result, str(uuid.uuid4()))

            checks = {
                "route": route in expected["route_any_of"],
                "tools": subset_ok(list(tools), expected["tools_required"]),
            }
            if expected.get("ambiguity_action_any_of"):
                checks["ambiguity"] = ambiguity_action in expected["ambiguity_action_any_of"]
            if expected.get("calculation_type_any_of") and calc_type:
                checks["calculation_type"] = calc_type in expected["calculation_type_any_of"]

            verdict = "PASS" if all(checks.values()) else "FAIL"
            results.append({
                "case_id": case["case_id"],
                "verdict": verdict,
                "checks": checks,
                "actual_route": route,
                "actual_tools": tools,
                "actual_ambiguity_action": ambiguity_action,
                "response_status": response.get("status"),
                "verification_verdict": (state.get("verification_report") or {}).get("verdict"),
                "safe_stop_reason": state.get("safe_stop_reason"),
                "answer": response.get("answer"),
                "sources": response.get("sources", []),
                "known_current_gaps": case.get("known_current_gaps", []),
            })
        except Exception as exc:
            results.append({
                "case_id": case["case_id"],
                "verdict": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "known_current_gaps": case.get("known_current_gaps", []),
            })
    return results

def write_outputs(results: list[dict[str, Any]], output_json: Path) -> None:
    summary = {
        "total": len(results),
        "pass": sum(r["verdict"] == "PASS" for r in results),
        "fail": sum(r["verdict"] == "FAIL" for r in results),
        "error": sum(r["verdict"] == "ERROR" for r in results),
    }
    output_json.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    csv_path = output_json.with_suffix(".csv")
    fields = [
        "case_id","verdict","actual_route","actual_tools","actual_ambiguity_action",
        "actual_calculation_type","response_status","verification_verdict",
        "safe_stop_reason","known_current_gaps","error"
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            cooked = dict(row)
            for key in ("actual_tools", "known_current_gaps"):
                if isinstance(cooked.get(key), list):
                    cooked[key] = "|".join(str(x) for x in cooked[key])
            writer.writerow(cooked)
    print(json.dumps(summary, ensure_ascii=False))
    print(f"JSON: {output_json}")
    print(f"CSV : {csv_path}")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("evaluation_dataset_v0.1_test_ready_no_legal_guardrail.json"),
    )
    parser.add_argument("--mode", choices=["deterministic", "full"], default="deterministic")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("evaluation_results_v0.1.json"))
    args = parser.parse_args()

    data = load_cases(args.dataset)
    if args.mode == "deterministic":
        results = deterministic_eval(args.repo_root.resolve(), data)
    else:
        results = full_eval(args.repo_root.resolve(), data, args.top_k)
    write_outputs(results, args.output)

if __name__ == "__main__":
    main()
