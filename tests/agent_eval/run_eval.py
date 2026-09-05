"""Live regression runner.

Uses the same public path as FastAPI POST /api/search and Streamlit chat:
PensionLangGraphAgent.respond(question, session_context=...).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.agent_eval.evaluators import hard_fails, map_intents, score_turn, source_types_from
from tests.agent_eval.reporter import write_csv, write_json, write_reproducibility, write_summary
from chatbot.observability import build_gold_turn_metadata

CASES_PATH = Path(__file__).with_name("test_cases.json")
REPORT_DIR = REPO_ROOT / "reports" / "agent_eval"


class EvalLangGraphAgent:
    """Thin wrapper that keeps last graph state without changing production."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.last_state: dict[str, Any] | None = None
        original = agent.invoke

        def tracked(question: str, top_k: int = 5, session_context: dict[str, Any] | None = None):
            self.last_state = original(question, top_k=top_k, session_context=session_context)
            return self.last_state

        agent.invoke = tracked  # type: ignore[method-assign]

    def respond(self, question: str, session_context: dict[str, Any] | None, question_id: str) -> dict[str, Any]:
        self.last_state = None
        return self.agent.respond(
            question,
            session_context=session_context,
            question_id=question_id,
        )


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["cases"] if isinstance(payload, dict) else payload


def hyperclova_configured() -> bool:
    return bool(os.getenv("CLOVA_STUDIO_API_KEY"))


def next_session(
    previous: dict[str, Any] | None,
    question: str,
    envelope: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """Mirror Streamlit app.py session updates so multi-turn uses the same contract."""
    meta = envelope.get("metadata") or {}
    missing = list(meta.get("missing_fields") or [])
    confirmed = dict((previous or {}).get("confirmed_constraints") or {})
    updates = dict(meta.get("context_updates") or {})
    confirmed.update(updates.get("confirmed_constraints") or {})
    route = meta.get("route") or (previous or {}).get("active_intent")
    topic = updates.get("last_topic") or (previous or {}).get("last_topic")
    upper_q = question.upper()
    for candidate in ("IRP", "DC", "DB", "연금저축"):
        if candidate.upper() in upper_q:
            topic = candidate
            break
    base = {
        "session_id": session_id,
        "pending_question_id": envelope.get("question_id") if envelope.get("status") == "clarify" else (previous or {}).get("pending_question_id"),
        "confirmed_constraints": confirmed,
        "expires_at": time.time() + 1800,
    }
    if envelope.get("status") == "clarify":
        base.update(
            {
                "missing_fields": missing,
                "pending_question": question,
                "active_intent": route,
                "last_topic": topic,
                "last_assistant_action": "CLARIFY",
                "last_candidates": updates.get("last_candidates", (previous or {}).get("last_candidates", [])),
                "selected_product": updates.get("selected_product", (previous or {}).get("selected_product")),
                "pending_task": updates.get("pending_task", (previous or {}).get("pending_task")),
            }
        )
        return base
    base.update(
        {
            "missing_fields": updates.get("missing_fields", (previous or {}).get("missing_fields", [])),
            "pending_question": (previous or {}).get("pending_question"),
            "active_intent": route,
            "last_topic": topic,
            "last_assistant_action": updates.get("last_assistant_action") or "ANSWER",
            "last_candidates": updates.get("last_candidates", (previous or {}).get("last_candidates", [])),
            "selected_product": updates.get("selected_product", (previous or {}).get("selected_product")),
            "pending_task": updates.get("pending_task", (previous or {}).get("pending_task")),
        }
    )
    return base


def collect_trace(
    *,
    question: str,
    envelope: dict[str, Any],
    state: dict[str, Any] | None,
    adapter: Any,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = envelope.get("metadata") or {}
    route = str(meta.get("route") or (state or {}).get("route") or "")
    tools = list((state or {}).get("tools") or meta.get("used_tools") or [])
    if not tools and route:
        tools = [part for part in route.replace("both", "document+product").split("+") if part]
    products = list((state or {}).get("product_results") or [])
    docs = list((state or {}).get("results") or [])
    law = (state or {}).get("law_result") or envelope.get("law_result") or {}
    product_names = [str(item.get("product_name") or "") for item in products if item.get("product_name")]
    grades = [item.get("risk_grade") for item in products]
    spec = None
    if adapter is not None and hasattr(adapter, "parse_query") and route.startswith("product"):
        resolved = str((state or {}).get("normalized_question") or question)
        spec = adapter.parse_query(resolved, limit=5)
    source_types = source_types_from(envelope, state)
    postgres_used = "postgres" in source_types or "product" in source_types or "product" in route
    enterprise = "enterprise_rag" in source_types or "document" in route
    external = "external_api" in source_types or "law" in route
    evidence_status = (envelope.get("evidence_summary") or {}).get("status") or envelope.get("status")
    session_view = dict(session or {})
    session_view.update(meta.get("context_updates") or {})
    intents = map_intents(
        route,
        str(envelope.get("status") or ""),
        str(meta.get("evidence_policy") or ""),
        question,
        session=session_view,
    )
    pronoun_resolved = bool(product_names) and any(token in question for token in ("그 상품", "이 상품", "그중"))
    trace_blob = (state or {}).get("product_execution_trace") or ((state or {}).get("final_result") or {}).get("langgraph", {}).get("product_execution_trace") or {}
    return {
        "detected_intent": intents,
        "actual_route": route,
        "tools_called": tools,
        "source_types": source_types,
        "source_documents": [
            item.get("filename") or item.get("source_file")
            for item in (docs + products)
            if item.get("filename") or item.get("source_file")
        ],
        "postgres_used": postgres_used,
        "enterprise_rag_used": enterprise,
        "external_api_used": external,
        "evidence_status": evidence_status,
        "product_names": product_names,
        "product_count": len(products),
        "product_risk_grades": grades,
        "query_sort_by": getattr(spec, "sort_by", None),
        "query_risk_max": getattr(spec, "risk_grade_max", None),
        "query_risk_tolerance": getattr(spec, "risk_tolerance", None),
        "query_allowed_risk_buckets": list(getattr(spec, "allowed_risk_buckets", ()) or ()),
        "candidate_ids": [str(item.get("record_id") or "") for item in products],
        "candidate_count": len(products),
        "selected_product_id": (
            str((products[0] or {}).get("record_id") or "")
            if products
            else str(((session_view.get("selected_product") or {}).get("record_id") or ""))
        ),
        "db_row_count": (
            (trace_blob or {}).get("db_rows_after_filter_count")
            or (trace_blob or {}).get("db_rows_raw_count")
        ),
        "normalization_status": [item.get("selected_performance_status") for item in products],
        "raw_units": [item.get("total_fee_unit") or item.get("selected_performance_unit") for item in products],
        "raw_values": [
            item.get("selected_performance_value")
            if item.get("selected_performance_value") is not None
            else item.get("total_fee")
            for item in products
        ],
        "display_values": [
            (
                (item.get("selected_performance_audit") or {}).get("display_value")
                if item.get("selected_performance_value") is not None
                else item.get("total_fee")
            )
            for item in products
        ],
        "ranking_breakdown": [item.get("ranking_breakdown") for item in products],
        "risk_buckets": [item.get("risk_bucket") for item in products],
        "pronoun_resolved": pronoun_resolved,
        "product_execution_trace": trace_blob,
        "safe_stop_reason": (
            (state or {}).get("safe_stop_reason")
            or ((state or {}).get("final_result") or {}).get("langgraph", {}).get("safe_stop_reason")
        ),
        "resolved_query": str((state or {}).get("normalized_question") or question),
        "latency_ms": None,
    }


def maybe_llm_judge(core: Any, enabled: bool, question: str, answer: str) -> dict[str, Any] | None:
    if not enabled or not answer:
        return None
    try:
        provider = core.answer_provider
        payload = {
            "question": question,
            "answer": answer,
            "instruction": "Score only usefulness. Ignore factual DB accuracy and routing.",
        }
        result = provider.structured(
            "Return JSON {\"answered\":0|1|2,\"natural\":0|1|2,\"sufficient\":0|1|2,\"usefulness\":0|1|2}. "
            "Judge only whether the answer addresses the user, sounds natural, and is sufficient. "
            "Do not judge database numbers, sources, or routing.",
            payload,
        )
        return {
            "answered": int(result.get("answered") or 0),
            "natural": int(result.get("natural") or 0),
            "sufficient": int(result.get("sufficient") or 0),
            "usefulness": int(result.get("usefulness") or 0),
        }
    except Exception as exc:
        return {"error": type(exc).__name__, "usefulness": 0}


def catalog_names(adapter: Any) -> set[str]:
    names: set[str] = set()
    for record in getattr(adapter, "records", []) or []:
        name = record.get("product_name")
        if name:
            names.add(str(name))
    return names


def run_cases(
    cases: list[dict[str, Any]],
    *,
    llm_judge: bool,
) -> dict[str, Any]:
    skip_reason = None
    if not hyperclova_configured():
        skip_reason = "CLOVA_STUDIO_API_KEY is not set; live LLM turns cannot run"
    client = None
    adapter = None
    catalog: set[str] = set()
    if skip_reason is None:
        try:
            os.environ.setdefault("PENSION_AGENT_MODE", "langgraph")
            from chatbot.agent_core import PensionAgentCore
            from chatbot.pension_langgraph_agent import PensionLangGraphAgent

            core = PensionAgentCore()
            client = EvalLangGraphAgent(PensionLangGraphAgent(core))
            adapter = core.product_db
            catalog = catalog_names(adapter)
        except Exception as exc:
            skip_reason = f"agent_init_failed:{type(exc).__name__}"

    turns: list[dict[str, Any]] = []
    case_verdicts: dict[str, str] = {}
    category_stats: dict[str, dict[str, int]] = {}

    for case in cases:
        case_id = case["id"]
        category = case.get("category") or "ungrouped"
        category_stats.setdefault(category, {"pass": 0, "total": 0, "skip": 0})
        category_stats[category]["total"] += 1
        if skip_reason:
            case_verdicts[case_id] = "SKIP"
            category_stats[category]["skip"] += 1
            for index, turn in enumerate(case["turns"], start=1):
                turns.append(
                    {
                        "test_id": case_id,
                        "turn_no": index,
                        "user_query": turn["user"],
                        "response_text": "",
                        "detected_intent": [],
                        "actual_route": None,
                        "expected_routes": (turn.get("expect") or {}).get("preferred_routes") or [],
                        "tools_called": [],
                        "source_types": [],
                        "source_documents": [],
                        "postgres_used": False,
                        "enterprise_rag_used": False,
                        "external_api_used": False,
                        "evidence_status": None,
                        "latency_ms": 0,
                        "exception": None,
                        "pass_fail": "SKIP",
                        "failure_reasons": [skip_reason],
                        "total": 0,
                        "context_lost": False,
                        "product_names": [],
                    }
                )
            continue

        session: dict[str, Any] | None = None
        session_id = f"eval-{case_id}-{uuid.uuid4().hex[:8]}"
        previous_products: list[str] = []
        case_fail = False
        assert client is not None
        for index, turn in enumerate(case["turns"], start=1):
            expect = turn.get("expect") or {}
            question = turn["user"]
            started = time.time()
            exception = None
            envelope: dict[str, Any] = {}
            try:
                envelope = client.respond(question, session, str(uuid.uuid4()))
            except Exception as exc:
                exception = f"{type(exc).__name__}: {exc}"
                envelope = {"status": "system_error", "answer": "", "metadata": {}, "sources": []}
            latency_ms = round((time.time() - started) * 1000, 1)
            state = client.last_state
            trace = collect_trace(question=question, envelope=envelope, state=state, adapter=adapter, session=session)
            session_after = next_session(session, question, envelope, session_id)
            fails = hard_fails(
                expect=expect,
                question=question,
                envelope=envelope,
                trace=trace,
                session_before=session,
                session_after=session_after,
                catalog=catalog,
                previous_products=previous_products,
            )
            if expect.get("keep_session") and session and session_after.get("session_id") != session.get("session_id"):
                context_lost = True
            else:
                context_lost = bool(expect.get("keep_session") and session is None and index > 1)
            judge = maybe_llm_judge(
                client.agent.legacy_agent,
                llm_judge,
                question,
                str(envelope.get("answer") or ""),
            )
            scored = score_turn(
                expect=expect,
                question=question,
                envelope=envelope,
                trace=trace,
                session_before=session,
                session_after=session_after,
                fails=fails,
                llm_judge=judge,
            )
            if scored["pass_fail"] == "FAIL":
                case_fail = True
            row = {
                "test_id": case_id,
                "turn_no": index,
                "user_query": question,
                "response_text": envelope.get("answer") or "",
                "detected_intent": trace["detected_intent"],
                "actual_route": trace["actual_route"],
                "expected_routes": expect.get("preferred_routes") or [],
                "tools_called": trace["tools_called"],
                "source_types": trace["source_types"],
                "source_documents": trace["source_documents"],
                "postgres_used": trace["postgres_used"],
                "enterprise_rag_used": trace["enterprise_rag_used"],
                "external_api_used": trace["external_api_used"],
                "evidence_status": trace["evidence_status"],
                "latency_ms": latency_ms,
                "exception": exception,
                "pass_fail": scored["pass_fail"],
                "failure_reasons": scored["failure_reasons"],
                "scores": scored["scores"],
                "total": scored["total"],
                "context_lost": context_lost,
                "product_names": trace["product_names"],
                "response_status": envelope.get("status"),
                "session_id": session_id,
                "confirmed_constraints": (session_after or {}).get("confirmed_constraints"),
                "gold_metadata": build_gold_turn_metadata(
                    question=question,
                    envelope=envelope,
                    state=state,
                    adapter=adapter,
                    session=session_after,
                    latency_ms=latency_ms,
                    test_id=case_id,
                ),
            }
            if exception:
                row["failure_reasons"] = list(row["failure_reasons"]) + [f"exception:{exception}"]
                row["pass_fail"] = "FAIL"
                case_fail = True
            turns.append(row)
            print(
                f"{case_id} T{index} {row['pass_fail']} route={row['actual_route']} "
                f"score={row['total']} {row['failure_reasons']}",
                flush=True,
            )
            session = session_after
            if trace["product_names"]:
                previous_products = list(trace["product_names"])

        case_verdicts[case_id] = "FAIL" if case_fail else "PASS"
        if case_verdicts[case_id] == "PASS":
            category_stats[category]["pass"] += 1

    scored_turns = [row for row in turns if row["pass_fail"] != "SKIP"]
    average = (
        round(sum(row.get("total") or 0 for row in scored_turns) / len(scored_turns), 2)
        if scored_turns
        else 0.0
    )
    summary = {
        "cases": len(cases),
        "turns": len(turns),
        "pass": sum(1 for value in case_verdicts.values() if value == "PASS"),
        "fail": sum(1 for value in case_verdicts.values() if value == "FAIL"),
        "skip": sum(1 for value in case_verdicts.values() if value == "SKIP"),
        "average_score": average,
        "entrypoint": "PensionLangGraphAgent.respond / POST /api/search",
        "llm_judge": llm_judge,
        "skip_reason": skip_reason,
    }
    category_out = {
        name: {
            "pass": stats["pass"],
            "total": stats["total"] - stats["skip"],
            "rate": (
                f"{round(100 * stats['pass'] / (stats['total'] - stats['skip']), 1)}%"
                if stats["total"] > stats["skip"]
                else "SKIP"
            ),
        }
        for name, stats in category_stats.items()
    }
    return {
        "summary": summary,
        "case_verdicts": case_verdicts,
        "category_stats": category_out,
        "turns": turns,
    }


def filter_cases(
    cases: list[dict[str, Any]],
    *,
    case_id: str | None,
    category: str | None,
    only: list[str] | None,
) -> list[dict[str, Any]]:
    selected = cases
    if only:
        wanted = set(only)
        selected = [case for case in selected if case["id"] in wanted]
    if case_id:
        selected = [case for case in selected if case["id"] == case_id]
    if category:
        selected = [case for case in selected if case.get("category") == category]
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Live pension-agent regression eval")
    parser.add_argument("--case", help="single case id, e.g. T002")
    parser.add_argument("--ids", help="comma-separated case ids, e.g. T001,T002,T015")
    parser.add_argument("--category", help="general_concept / recommendation / ...")
    parser.add_argument("--all", action="store_true", help="run every case")
    parser.add_argument("--llm-judge", action="store_true", help="optional usefulness judge")
    parser.add_argument("--repeat", type=int, default=1, help="how many full runs (2 writes reproducibility.md)")
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    judge = args.llm_judge or os.getenv("AGENT_EVAL_LLM_JUDGE", "false").lower() == "true"
    cases = load_cases(CASES_PATH)
    ids = [item.strip() for item in args.ids.split(",") if item.strip()] if args.ids else None
    if ids:
        selected = filter_cases(cases, case_id=args.case, category=args.category, only=ids)
    elif args.case or args.category:
        selected = filter_cases(cases, case_id=args.case, category=args.category, only=None)
    elif args.all:
        selected = cases
    else:
        selected = filter_cases(cases, case_id=None, category=None, only=["T001", "T002", "T003", "T004", "T005", "T006"])

    if not selected:
        raise SystemExit("no cases selected")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for index in range(max(1, args.repeat)):
        print(f"=== run {index + 1}/{args.repeat} cases={[case['id'] for case in selected]} ===", flush=True)
        result = run_cases(selected, llm_judge=judge)
        runs.append(result)
        if index == 0:
            write_json(output_dir / "latest_results.json", result)
            write_csv(output_dir / "latest_results.csv", result["turns"])
            write_summary(output_dir / "latest_summary.md", result)
        else:
            write_json(output_dir / f"run_{index + 1}_results.json", result)

    if len(runs) >= 2:
        write_reproducibility(output_dir / "reproducibility.md", runs[0], runs[1])

    last = runs[-1]["summary"]
    print(json.dumps(last, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
