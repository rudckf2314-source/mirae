"""Write JSON / CSV / markdown reports without secrets."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, turns: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "test_id", "turn_no", "user_query", "pass_fail", "total",
        "detected_intent", "actual_route", "expected_routes",
        "tools_called", "source_types", "postgres_used", "enterprise_rag_used",
        "external_api_used", "evidence_status", "latency_ms", "exception",
        "failure_reasons", "response_text",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in turns:
            cooked = dict(row)
            for key in ("detected_intent", "tools_called", "source_types", "failure_reasons", "expected_routes"):
                if isinstance(cooked.get(key), list):
                    cooked[key] = "|".join(str(item) for item in cooked[key])
            writer.writerow(cooked)


def _clip(text: str, limit: int = 280) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = payload["summary"]
    turns = payload["turns"]
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in turns:
        by_case[row["test_id"]].append(row)

    lines = [
        "# Pension Agent live eval",
        "",
        f"- cases: {summary['cases']}",
        f"- turns: {summary['turns']}",
        f"- PASS: {summary['pass']} / FAIL: {summary['fail']} / SKIP: {summary['skip']}",
        f"- average score: {summary['average_score']}",
        "",
        "## Category pass rate",
        "",
    ]
    for category, stats in sorted((payload.get("category_stats") or {}).items()):
        lines.append(f"- {category}: {stats['pass']}/{stats['total']} ({stats['rate']})")
    lines.extend(["", "## Failed cases", ""])
    failed = [case_id for case_id, rows in by_case.items() if any(row["pass_fail"] == "FAIL" for row in rows)]
    if not failed:
        lines.append("- none")
    for case_id in failed:
        rows = by_case[case_id]
        reasons = []
        for row in rows:
            reasons.extend(row.get("failure_reasons") or [])
        lines.append(f"### {case_id}")
        lines.append(f"- reasons: {', '.join(dict.fromkeys(reasons)) or 'score'}")
        for row in rows:
            lines.append(
                f"- T{row['turn_no']}: actual_route=`{row.get('actual_route')}` "
                f"expected=`{row.get('expected_routes')}` sources=`{row.get('source_types')}`"
            )
            lines.append(f"  - context lost: {row.get('context_lost', False)}")
            lines.append(f"  - answer: {_clip(str(row.get('response_text') or ''))}")
        lines.append("")

    lines.extend(["## Representative answers", ""])
    for case_id in sorted(by_case):
        last = by_case[case_id][-1]
        lines.append(f"- {case_id} ({last['pass_fail']}): {_clip(str(last.get('response_text') or ''), 160)}")

    lines.extend(["", "## Improvement priority", ""])
    counts: dict[str, int] = defaultdict(int)
    for row in turns:
        for reason in row.get("failure_reasons") or []:
            key = str(reason).split(":")[0]
            counts[key] += 1
    if not counts:
        lines.append("- no hard failures recorded")
    else:
        for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reproducibility(path: Path, run_a: dict[str, Any], run_b: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    a = {(row["test_id"], row["turn_no"]): row for row in run_a["turns"]}
    b = {(row["test_id"], row["turn_no"]): row for row in run_b["turns"]}
    keys = sorted(set(a) | set(b))
    lines = [
        "# Reproducibility",
        "",
        f"- run A pass/fail/skip: {run_a['summary']['pass']}/{run_a['summary']['fail']}/{run_a['summary']['skip']}",
        f"- run B pass/fail/skip: {run_b['summary']['pass']}/{run_b['summary']['fail']}/{run_b['summary']['skip']}",
        "",
    ]
    intent_flip = route_flip = product_flip = context_flip = 0
    for key in keys:
        left, right = a.get(key), b.get(key)
        if not left or not right:
            continue
        if left.get("detected_intent") != right.get("detected_intent"):
            intent_flip += 1
            lines.append(f"- intent flip {key}: {left.get('detected_intent')} vs {right.get('detected_intent')}")
        if left.get("actual_route") != right.get("actual_route"):
            route_flip += 1
            lines.append(f"- route flip {key}: {left.get('actual_route')} vs {right.get('actual_route')}")
        if left.get("product_names") != right.get("product_names"):
            product_flip += 1
            lines.append(f"- candidate flip {key}: {left.get('product_names')} vs {right.get('product_names')}")
        if left.get("context_lost") != right.get("context_lost"):
            context_flip += 1
            lines.append(f"- context flip {key}: {left.get('context_lost')} vs {right.get('context_lost')}")
    lines[4:4] = [
        f"- intent flips: {intent_flip}",
        f"- route flips: {route_flip}",
        f"- candidate flips: {product_flip}",
        f"- context-resolution flips: {context_flip}",
        "",
        "## Differences",
        "",
    ]
    if intent_flip + route_flip + product_flip + context_flip == 0:
        lines.append("- no intent/route/candidate/context flips between the two runs")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
