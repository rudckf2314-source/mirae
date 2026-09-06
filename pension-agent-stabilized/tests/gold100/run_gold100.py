"""Run Gold-100 blind baseline against PensionLangGraphAgent.

Does not modify T001–T022. Does not mutate the Excel workbook.
First execution is a blind baseline — do not tune code from these scores.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.agent_eval.run_eval import EvalLangGraphAgent, catalog_names, collect_trace, next_session
from tests.gold100.excel_adapter import (
    DEFAULT_EXCEL,
    load_excel,
    validate_payload,
    write_intermediate,
)
from tests.gold100.gold100_evaluator import evaluate_case, EVALUATOR_VERSION

OUT_DIR = REPO_ROOT / "reports" / "gold100"
DEFAULT_CASES = Path(__file__).parent / "fixtures" / "cases.json"


def evaluation_manifest(payload: dict[str, Any]) -> dict[str, str]:
    """Bind repeated runs to unchanged code, source data, dataset and criteria."""
    digest = hashlib.sha256()
    for folder in ("chatbot", "data", "config", "tests/gold100", "tests/agent_eval"):
        for path in sorted((REPO_ROOT / folder).rglob("*")):
            relative = path.relative_to(REPO_ROOT)
            if not path.is_file() or any(p in {"__pycache__", "cache"} for p in relative.parts):
                continue
            if path.suffix not in {".py", ".json", ".jsonl", ".db"}:
                continue
            digest.update(relative.as_posix().encode())
            digest.update(path.read_bytes())
    for name in ("requirements.txt", "constraints.txt", "web_app.py", "app.py"):
        path = REPO_ROOT / name
        if path.exists():
            digest.update(path.read_bytes())
    dataset = json.dumps(payload["cases"], ensure_ascii=False, sort_keys=True).encode()
    from chatbot.legal_store import serving_date
    config = {name: os.getenv(name) for name in (
        "CLOVA_MODEL", "CLOVA_BASE_URL", "SUPERVISOR_MODEL", "LLM_TIMEOUT",
        "PRODUCT_DB_BACKEND", "STANDARD_JSON_DIR", "LEGAL_DB_PATH",
        "LAW_QUERY_FALLBACK_API", "COMPETITION_MODE", "LLM_PROVIDER",
    )}
    digest.update(json.dumps(config, sort_keys=True).encode())
    return {"evaluator_version": EVALUATOR_VERSION,
            "serving_date": serving_date(),
            "code_data_sha256": digest.hexdigest(),
            "dataset_sha256": hashlib.sha256(dataset).hexdigest()}


def competition_ready() -> tuple[bool, str | None]:
    if not os.getenv("CLOVA_STUDIO_API_KEY"):
        return False, "CLOVA_STUDIO_API_KEY is not set"
    return True, None


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            cooked = dict(row)
            for key, value in list(cooked.items()):
                if isinstance(value, (list, dict)):
                    cooked[key] = json.dumps(value, ensure_ascii=False)
            writer.writerow(cooked)


def rate(pass_n: int, total: int) -> str:
    if total <= 0:
        return "n/a"
    return f"{(pass_n / total) * 100:.1f}%"


def build_reports(payload: dict[str, Any], turns: list[dict[str, Any]], run_meta: dict[str, Any]) -> dict[str, Any]:
    case_verdicts: dict[str, str] = {}
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0, "total": 0})
    by_difficulty: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0, "total": 0})
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0, "total": 0})
    by_set: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0, "total": 0})

    route_hits = route_total = 0
    source_hits = source_total = 0
    fact_ratios: list[float] = []
    cite_hits = cite_total = 0
    product_hits = product_total = 0
    legal_calc_hits = legal_calc_total = 0
    abstention_hits = abstention_total = 0
    halluc_count = 0
    latencies: list[float] = []
    failure_counter: dict[str, int] = defaultdict(int)

    for turn in turns:
        tid = turn["test_id"]
        case_verdicts[tid] = turn["pass_fail"]
        cat = turn.get("유형") or "unknown"
        diff = turn.get("난이도") or "unknown"
        fam = turn.get("family") or "Other"
        set_name = turn.get("세트") or "unknown"
        for bucket in (by_category[cat], by_difficulty[diff], by_family[fam], by_set[set_name]):
            bucket["total"] += 1
            if turn["pass_fail"] == "PASS":
                bucket["pass"] += 1
            else:
                bucket["fail"] += 1

        latencies.append(float(turn.get("latency_ms") or 0))
        for reason in turn.get("failure_reasons") or []:
            failure_counter[reason.split(":")[0] + ":" + reason.split(":")[1] if reason.count(":") >= 1 else reason] += 1
            # also full code tallies for top failures
            failure_counter[reason] += 1

        eval_block = turn.get("evaluation") or {}
        if turn.get("adapter_route_families"):
            route_total += 1
            if eval_block.get("route_ok"):
                route_hits += 1
        hints = turn.get("adapter_eval_hints") or {}
        if hints.get("require_enterprise_document") or hints.get("require_postgres"):
            source_total += 1
            ok = True
            if hints.get("require_postgres") and not turn.get("structured_product_used"):
                ok = False
            if hints.get("require_enterprise_document") and not turn.get("enterprise_rag_used"):
                # allow document domain via source_types
                if "enterprise_document" not in (turn.get("source_types") or []) and "enterprise_rag" not in (
                    turn.get("source_types") or []
                ):
                    ok = False
            if ok:
                source_hits += 1
        cov = (eval_block.get("fact_coverage") or {}).get("coverage")
        if cov is not None:
            fact_ratios.append(float(cov))
        cite_total += 1
        if (eval_block.get("citation") or {}).get("covered"):
            cite_hits += 1
        if fam == "Product":
            product_total += 1
            if turn["pass_fail"] == "PASS":
                product_hits += 1
        if fam in {"Legal", "Calculation"}:
            legal_calc_total += 1
            if turn["pass_fail"] == "PASS":
                legal_calc_hits += 1
        if fam == "Abstention" or hints.get("require_abstention_or_clarify"):
            abstention_total += 1
            if turn["pass_fail"] == "PASS":
                abstention_hits += 1
        if any("hallucin" in r or "invented" in r or "arbitrary" in r or "unrelated" in r for r in (turn.get("failure_reasons") or [])):
            halluc_count += 1

    passed = sum(1 for v in case_verdicts.values() if v == "PASS")
    failed = sum(1 for v in case_verdicts.values() if v == "FAIL")
    skipped = sum(1 for v in case_verdicts.values() if v == "SKIP")

    def pack(stats: dict[str, dict[str, int]]) -> dict[str, Any]:
        out = {}
        for key, val in sorted(stats.items()):
            out[key] = {
                **val,
                "pass_rate": rate(val["pass"], val["total"]),
            }
        return out

    summary = {
        "label": "gold100_blind_baseline",
        "cases": len(case_verdicts),
        "pass": passed,
        "fail": failed,
        "skip": skipped,
        "overall_pass_rate": rate(passed, passed + failed) if (passed + failed) else "n/a",
        "routing_accuracy": rate(route_hits, route_total),
        "source_selection_accuracy": rate(source_hits, source_total),
        "required_fact_coverage_avg": round(sum(fact_ratios) / len(fact_ratios), 4) if fact_ratios else None,
        "grounding_citation_coverage": rate(cite_hits, cite_total),
        "product_retrieval_accuracy": rate(product_hits, product_total),
        "legal_calculation_accuracy": rate(legal_calc_hits, legal_calc_total),
        "correct_abstention_rate": rate(abstention_hits, abstention_total),
        "hallucination_cases": halluc_count,
        "latency_ms_avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "latency_ms_p50": sorted(latencies)[len(latencies) // 2] if latencies else None,
        "latency_ms_max": max(latencies) if latencies else None,
        "entrypoint": "PensionLangGraphAgent.respond",
        "competition_mode": os.getenv("COMPETITION_MODE", "1"),
        "llm_provider": os.getenv("LLM_PROVIDER", "hyperclova"),
        **run_meta,
    }

    return {
        "summary": summary,
        "case_verdicts": case_verdicts,
        "category_stats": pack(by_category),
        "difficulty_stats": pack(by_difficulty),
        "family_stats": pack(by_family),
        "set_stats": pack(by_set),
        "failure_counter": dict(sorted(failure_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        "turns": turns,
        "source_manifest": {
            "excel": payload.get("source_excel"),
            "intermediate_json": str(OUT_DIR / "gold100_cases.json"),
            "excel_modified": False,
            "regression_suite_modified": False,
        },
    }


def write_summary_md(path: Path, result: dict[str, Any]) -> None:
    s = result["summary"]
    lines = [
        "# Gold-100 Blind Baseline Summary",
        "",
        "> First execution baseline. Scores are not used to weaken evaluators.",
        "",
        f"- cases: {s['cases']}",
        f"- PASS / FAIL / SKIP: {s['pass']} / {s['fail']} / {s['skip']}",
        f"- overall PASS rate: **{s['overall_pass_rate']}**",
        f"- routing accuracy: {s['routing_accuracy']}",
        f"- source selection accuracy: {s['source_selection_accuracy']}",
        f"- required fact coverage avg: {s['required_fact_coverage_avg']}",
        f"- grounding / citation coverage: {s['grounding_citation_coverage']}",
        f"- product retrieval accuracy: {s['product_retrieval_accuracy']}",
        f"- legal / calculation accuracy: {s['legal_calculation_accuracy']}",
        f"- correct abstention: {s['correct_abstention_rate']}",
        f"- hallucination cases: {s['hallucination_cases']}",
        f"- latency avg / p50 / max ms: {s['latency_ms_avg']} / {s['latency_ms_p50']} / {s['latency_ms_max']}",
        f"- provider: `{s['llm_provider']}` competition_mode=`{s['competition_mode']}`",
        "",
        "## Difficulty PASS rate",
        "",
    ]
    for key, val in (result.get("difficulty_stats") or {}).items():
        lines.append(f"- {key}: {val['pass']}/{val['total']} ({val['pass_rate']})")
    lines.extend(["", "## Family PASS rate", ""])
    for key, val in (result.get("family_stats") or {}).items():
        lines.append(f"- {key}: {val['pass']}/{val['total']} ({val['pass_rate']})")
    lines.extend(["", "## Set PASS rate", ""])
    for key, val in (result.get("set_stats") or {}).items():
        lines.append(f"- {key}: {val['pass']}/{val['total']} ({val['pass_rate']})")
    lines.extend(["", "## Category PASS rate (Excel 유형)", ""])
    for key, val in (result.get("category_stats") or {}).items():
        lines.append(f"- {key}: {val['pass']}/{val['total']} ({val['pass_rate']})")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_failures_md(path: Path, result: dict[str, Any]) -> None:
    lines = ["# Gold-100 Failures", ""]
    fails = [t for t in result["turns"] if t.get("pass_fail") == "FAIL"]
    lines.append(f"Failed cases: {len(fails)}")
    lines.append("")
    for turn in fails:
        lines.append(f"## {turn['test_id']} · {turn.get('유형_난이도_원문')}")
        lines.append(f"- family: {turn.get('family')}")
        lines.append(f"- status/route: `{turn.get('response_status')}` / `{turn.get('actual_route')}`")
        lines.append(f"- reasons: {', '.join(turn.get('failure_reasons') or [])}")
        lines.append(f"- question: {turn.get('user_query')}")
        lines.append(f"- expected: {turn.get('기대_답변')}")
        ans = (turn.get("response_text") or "").replace("\n", " ")
        lines.append(f"- answer: {ans[:400]}{'…' if len(ans) > 400 else ''}")
        lines.append("")
    if not fails:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reproducibility_md(path: Path, result: dict[str, Any]) -> None:
    s = result["summary"]
    lines = [
        "# Gold-100 Reproducibility",
        "",
        "Blind baseline — single run recorded. Repeat later to measure flips.",
        "",
        f"- run_id: `{s.get('run_id')}`",
        f"- started_at: {s.get('started_at')}",
        f"- finished_at: {s.get('finished_at')}",
        f"- overall: {s['pass']}/{s['cases']} ({s['overall_pass_rate']})",
        f"- excel: `{result['source_manifest']['excel']}`",
        f"- intermediate: `{result['source_manifest']['intermediate_json']}`",
        "- excel_modified: false",
        "- regression_suite_modified: false",
        "- evaluator_loosened: false",
        "",
        "To compare a second run, re-execute `python -m tests.gold100.run_gold100 --repeat 2`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_once(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready, reason = competition_ready()
    if not ready:
        raise RuntimeError(reason)

    os.environ.setdefault("PENSION_AGENT_MODE", "langgraph")
    os.environ.setdefault("COMPETITION_MODE", "1")
    os.environ.setdefault("LLM_PROVIDER", "hyperclova")

    from chatbot.agent_core import PensionAgentCore
    from chatbot.pension_langgraph_agent import PensionLangGraphAgent

    core = PensionAgentCore()
    client = EvalLangGraphAgent(PensionLangGraphAgent(core))
    adapter = core.product_db
    catalog = catalog_names(adapter)

    turns: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        tid = case["test_id"]
        question = case["테스트_질문"]
        print(f"[{index}/{len(cases)}] {tid} …", flush=True)
        session = None
        session_id = f"gold100-{tid.replace(' ', '')}-{uuid.uuid4().hex[:8]}"
        started = time.time()
        exception = None
        envelope: dict[str, Any] = {}
        try:
            envelope = client.respond(question, session, str(uuid.uuid4()))
        except Exception as exc:  # noqa: BLE001 — baseline must record failures
            exception = type(exc).__name__
            envelope = {"status": "system_error", "answer": "", "metadata": {}, "sources": []}
        latency_ms = round((time.time() - started) * 1000, 1)
        state = client.last_state
        trace = collect_trace(question=question, envelope=envelope, state=state, adapter=adapter, session=session)
        _ = next_session(session, question, envelope, session_id)
        evaluation = evaluate_case(case=case, envelope=envelope, trace=trace, catalog=catalog)
        if exception:
            evaluation["failure_reasons"] = [f"X:exception:{exception}", *evaluation["failure_reasons"]]
            evaluation["pass_fail"] = "FAIL"

        print(
            f"  -> {evaluation['pass_fail']} route={trace.get('actual_route')} "
            f"status={envelope.get('status')} {latency_ms}ms "
            f"{evaluation['failure_reasons'][:2]}",
            flush=True,
        )
        turns.append(
            {
                "test_id": tid,
                "turn_no": 1,
                "세트": case.get("세트"),
                "유형": case.get("유형"),
                "난이도": case.get("난이도"),
                "유형_난이도_원문": case.get("유형_난이도_원문"),
                "family": case.get("family"),
                "user_query": question,
                "기대_답변": case.get("기대_답변"),
                "expected_numbers_from_기대답변": case.get("expected_numbers_from_기대답변"),
                "adapter_eval_hints": case.get("adapter_eval_hints"),
                "adapter_route_families": (case.get("adapter_eval_hints") or {}).get("route_families"),
                "response_text": envelope.get("answer") or "",
                "response_status": envelope.get("status"),
                "detected_intent": trace.get("detected_intent"),
                "actual_route": trace.get("actual_route"),
                "tools_called": trace.get("tools_called"),
                "source_types": trace.get("source_types"),
                "source_documents": trace.get("source_documents"),
                "postgres_used": trace.get("postgres_used"),
                "product_backend": trace.get("product_backend"),
                "structured_product_used": trace.get("structured_product_used"),
                "enterprise_rag_used": trace.get("enterprise_rag_used"),
                "external_api_used": trace.get("external_api_used"),
                "product_names": trace.get("product_names"),
                "product_count": trace.get("product_count"),
                "latency_ms": latency_ms,
                "exception": exception,
                "pass_fail": evaluation["pass_fail"],
                "failure_reasons": evaluation["failure_reasons"],
                "evaluation": evaluation,
            }
        )
    return turns


def main() -> None:
    parser = argparse.ArgumentParser(description="Gold-100 blind baseline eval")
    parser.add_argument("--excel", type=Path, default=None)
    parser.add_argument("--cases-json", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="parse/validate only")
    parser.add_argument("--allow-live", action="store_true", help="explicitly allow billable HyperCLOVA requests")
    parser.add_argument("--limit", type=int, default=0, help="optional first-N for smoke")
    parser.add_argument("--ids", type=str, default="", help="comma-separated Test ids")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if not args.dry_run and not args.allow_live:
        parser.error("Use --dry-run, or explicitly authorize API costs with --allow-live.")

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    os.environ.setdefault("COMPETITION_MODE", "1")
    os.environ.setdefault("LLM_PROVIDER", "hyperclova")

    out_dir = args.out_dir
    if out_dir.exists() and any(out_dir.iterdir()):
        parser.error("Output directory must be new or empty; historical results are never overwritten.")
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = load_excel(args.excel) if args.excel else json.loads(args.cases_json.read_text(encoding="utf-8"))
    cases_path = out_dir / "gold100_cases.json"
    write_intermediate(payload, cases_path)
    issues = validate_payload(payload)
    dry_path = out_dir / "gold100_parser_validation.json"
    dry_path.write_text(
        json.dumps({"ok": not issues, "issues": issues, "case_count": payload["case_count"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"parser_validation": "PASS" if not issues else "FAIL", "issues": issues}, ensure_ascii=False), flush=True)
    if issues:
        raise SystemExit(2)
    manifest = evaluation_manifest(payload)
    (out_dir / "evaluation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if args.dry_run:
        print("dry-run complete", flush=True)
        return

    cases = list(payload["cases"])
    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        cases = [c for c in cases if c["test_id"] in wanted]
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        parser.error("No cases selected.")

    runs: list[dict[str, Any]] = []
    for run_idx in range(max(1, args.repeat)):
        if evaluation_manifest(payload) != manifest:
            raise RuntimeError("Code/data changed between runs; comparison aborted.")
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        run_id = uuid.uuid4().hex[:12]
        print(f"=== gold100 run {run_idx + 1}/{args.repeat} id={run_id} n={len(cases)} ===", flush=True)
        turns = run_once(cases)
        finished_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        result = build_reports(
            payload,
            turns,
            {"run_id": run_id, "started_at": started_at, "finished_at": finished_at, "executed_count": len(turns), **manifest},
        )
        runs.append(result)

        # Required output filenames (first run is the blind baseline).
        if run_idx == 0:
            (out_dir / "gold100_results.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            write_csv_rows(
                out_dir / "gold100_results.csv",
                turns,
                [
                    "test_id",
                    "세트",
                    "유형",
                    "난이도",
                    "family",
                    "pass_fail",
                    "actual_route",
                    "response_status",
                    "postgres_used",
                    "enterprise_rag_used",
                    "latency_ms",
                    "failure_reasons",
                    "user_query",
                    "기대_답변",
                    "response_text",
                ],
            )
            # category stats csv
            cat_rows = [
                {"category": k, "pass": v["pass"], "fail": v["fail"], "total": v["total"], "pass_rate": v["pass_rate"]}
                for k, v in (result.get("category_stats") or {}).items()
            ]
            write_csv_rows(
                out_dir / "gold100_category_stats.csv",
                cat_rows,
                ["category", "pass", "fail", "total", "pass_rate"],
            )
            write_summary_md(out_dir / "gold100_summary.md", result)
            write_failures_md(out_dir / "gold100_failures.md", result)
            write_reproducibility_md(out_dir / "gold100_reproducibility.md", result)
            # freeze blind baseline copy
            (out_dir / "gold100_blind_baseline.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            (out_dir / f"gold100_run_{run_idx + 1}_results.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    print(json.dumps(runs[-1]["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
