from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(r"c:\mirae\reports\gold100_retest_20260905_2217")
BASE = Path(r"c:\mirae\reports\gold100_hyperclova_20260905")
KST = timezone(timedelta(hours=9))

result = json.loads((OUT / "gold100_results.json").read_text(encoding="utf-8"))
turns = result["turns"]
summary = result["summary"]

ids = [t["test_id"] for t in turns]
uniq = set(ids)
missing = [f"Test {i}" for i in range(1, 101) if f"Test {i}" not in uniq]
dupes = sorted({i for i in ids if ids.count(i) > 1})
pf = sum(1 for t in turns if t.get("pass_fail") == "PASS")
ff = sum(1 for t in turns if t.get("pass_fail") == "FAIL")
sk = sum(1 for t in turns if t.get("pass_fail") == "SKIP")
integrity = {
    "total_rows": len(turns),
    "unique_test_ids": len(uniq),
    "duplicates": dupes,
    "missing": missing,
    "PASS": pf,
    "FAIL": ff,
    "SKIP": sk,
    "sum_equals_100": (pf + ff + sk) == 100 and len(turns) == 100 and not dupes and not missing,
}

INTERNAL_TOKENS = [
    "risk_tolerance",
    "investment_horizon",
    "holding_product_name",
    "account_type",
    "personal_legal_facts",
    "product_reference",
    "ENTERPRISE_DOCUMENT",
]
CODE_PATTERNS = [
    re.compile(r"\b[A-Z]{1,3}:[a-z0-9_]+\b"),
    re.compile(r"```"),
    re.compile(r"\{[^\n]{0,40}\"[a-z_]+\"\s*:"),
]
ALLOW = re.compile(r"\b(IRP|DC|DB|TDF|ETF)\b")

exposure_hits = []
for t in turns:
    answer = str(t.get("response_text") or "")
    hits = []
    for tok in INTERNAL_TOKENS:
        if tok in answer:
            hits.append({"kind": "internal_field", "token": tok})
    for pat in CODE_PATTERNS:
        for m in pat.finditer(answer):
            frag = m.group(0)
            if ALLOW.search(frag):
                continue
            hits.append({"kind": "code_or_json_pattern", "token": frag[:80]})
    seen = set()
    uniq_hits = []
    for h in hits:
        key = (h["kind"], h["token"])
        if key in seen:
            continue
        seen.add(key)
        uniq_hits.append(h)
    if uniq_hits:
        exposure_hits.append(
            {
                "test_id": t["test_id"],
                "pass_fail_unchanged": t.get("pass_fail"),
                "hits": uniq_hits,
                "answer_preview": answer.replace("\n", " ")[:240],
            }
        )

exposure_report = {
    "scope": "user-facing response_text only (not metadata)",
    "pass_fail_not_modified": True,
    "cases_with_exposure": len(exposure_hits),
    "total_cases_scanned": len(turns),
    "tokens_checked": INTERNAL_TOKENS,
    "hits": exposure_hits,
}
(OUT / "gold100_code_exposure_report.json").write_text(
    json.dumps(exposure_report, ensure_ascii=False, indent=2), encoding="utf-8"
)

exp_md = [
    "# Gold-100 Code Exposure Inspection",
    "",
    "> User-facing `response_text` only. PASS/FAIL verdicts were **not** changed.",
    "",
    f"- scanned: {len(turns)}",
    f"- cases with possible exposure: **{len(exposure_hits)}**",
    "",
    "## Hits",
    "",
]
if not exposure_hits:
    exp_md.append("- none")
else:
    for item in exposure_hits:
        toks = ", ".join(f"{h['kind']}:{h['token']}" for h in item["hits"][:8])
        exp_md.append(f"### {item['test_id']} (verdict={item['pass_fail_unchanged']})")
        exp_md.append(f"- hits: {toks}")
        exp_md.append(f"- preview: {item['answer_preview']}")
        exp_md.append("")
(OUT / "gold100_code_exposure_report.md").write_text("\n".join(exp_md) + "\n", encoding="utf-8")

base = json.loads((BASE / "gold100_results.json").read_text(encoding="utf-8"))
base_turns = {t["test_id"]: t for t in base["turns"]}
new_turns = {t["test_id"]: t for t in turns}
base_sum = base["summary"]


def sort_key(tid: str):
    parts = tid.split()
    return int(parts[-1]) if parts[-1].isdigit() else tid


flip_fp = []
flip_pf = []
for tid in sorted(new_turns, key=sort_key):
    b = base_turns.get(tid)
    n = new_turns[tid]
    if not b:
        continue
    if b.get("pass_fail") == "FAIL" and n.get("pass_fail") == "PASS":
        flip_fp.append(tid)
    if b.get("pass_fail") == "PASS" and n.get("pass_fail") == "FAIL":
        flip_pf.append(tid)

safe_stop_base = sum(1 for t in base["turns"] if t.get("response_status") == "safe_stop")
safe_stop_new = sum(1 for t in turns if t.get("response_status") == "safe_stop")
started = summary.get("started_at")
finished = summary.get("finished_at")

env_path = OUT / "run_environment.json"
env = json.loads(env_path.read_text(encoding="utf-8-sig")) if env_path.exists() else {}
env["recorded_at_end"] = datetime.now(KST).isoformat()
env["summary_started_at"] = started
env["summary_finished_at"] = finished
env["integrity"] = integrity
env_path.write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")

cmp = {
    "baseline_dir": str(BASE),
    "retest_dir": str(OUT),
    "baseline": {
        "PASS": base_sum.get("pass"),
        "FAIL": base_sum.get("fail"),
        "SKIP": base_sum.get("skip"),
        "overall_pass_rate": base_sum.get("overall_pass_rate"),
        "routing_accuracy": base_sum.get("routing_accuracy"),
        "latency_ms_avg": base_sum.get("latency_ms_avg"),
        "safe_stop_count": safe_stop_base,
    },
    "retest": {
        "PASS": summary.get("pass"),
        "FAIL": summary.get("fail"),
        "SKIP": summary.get("skip"),
        "overall_pass_rate": summary.get("overall_pass_rate"),
        "routing_accuracy": summary.get("routing_accuracy"),
        "latency_ms_avg": summary.get("latency_ms_avg"),
        "safe_stop_count": safe_stop_new,
        "started_at": started,
        "finished_at": finished,
    },
    "delta_pass": (summary.get("pass") or 0) - (base_sum.get("pass") or 0),
    "FAIL_to_PASS": flip_fp,
    "PASS_to_FAIL": flip_pf,
    "integrity": integrity,
    "code_exposure_cases": len(exposure_hits),
    "models": env.get("models"),
    "caveat": (
        "Single re-run score delta is not conclusive performance improvement; "
        "display polish vs content change must be distinguished."
    ),
}
(OUT / "gold100_comparison.json").write_text(json.dumps(cmp, ensure_ascii=False, indent=2), encoding="utf-8")

lines = [
    "# Gold-100 Retest Comparison Report",
    "",
    f"- baseline: `{BASE}` (46 PASS / 54 FAIL / 0 SKIP, ~8.36s)",
    f"- retest: `{OUT}`",
    f"- started: {started}",
    f"- finished: {finished}",
    "- models: answer=HCX-005, supervisor=HCX-007, normalizer=HCX-DASH-002, extraction=HCX-005",
    "",
    "## Integrity",
    "",
    (
        f"- rows={integrity['total_rows']} unique={integrity['unique_test_ids']} "
        f"duplicates={integrity['duplicates'] or 'none'} missing={integrity['missing'] or 'none'}"
    ),
    f"- PASS/FAIL/SKIP = {pf}/{ff}/{sk} (sum_ok={integrity['sum_equals_100']})",
    "",
    "## Overall",
    "",
    "| metric | baseline | retest |",
    "|---|---:|---:|",
    f"| PASS | {base_sum.get('pass')} | {summary.get('pass')} |",
    f"| FAIL | {base_sum.get('fail')} | {summary.get('fail')} |",
    f"| SKIP | {base_sum.get('skip')} | {summary.get('skip')} |",
    f"| overall PASS rate | {base_sum.get('overall_pass_rate')} | {summary.get('overall_pass_rate')} |",
    f"| routing accuracy | {base_sum.get('routing_accuracy')} | {summary.get('routing_accuracy')} |",
    f"| latency avg (ms) | {base_sum.get('latency_ms_avg')} | {summary.get('latency_ms_avg')} |",
    f"| safe_stop count | {safe_stop_base} | {safe_stop_new} |",
    "",
    f"- PASS delta: {cmp['delta_pass']:+d}",
    "",
    "## FAIL → PASS",
    "",
]
if flip_fp:
    lines.extend(f"- {tid}" for tid in flip_fp)
else:
    lines.append("- none")
lines.extend(["", "## PASS → FAIL", ""])
if flip_pf:
    lines.extend(f"- {tid}" for tid in flip_pf)
else:
    lines.append("- none")
lines.extend(
    [
        "",
        "## Interpretation notes",
        "",
        "- Do **not** treat a single re-run delta as confirmed capability improvement.",
        "- Natural-language display polish can change surface text without changing retrieval/routing substance.",
        "- Content-level flips (FAIL↔PASS) should be reviewed case-by-case against expected answers.",
        f"- User-facing code-exposure cases (report only): {len(exposure_hits)} — verdicts unchanged.",
        "",
    ]
)
(OUT / "gold100_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
(OUT / "gold100_integrity.json").write_text(json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8")

print(
    json.dumps(
        {
            "integrity_ok": integrity["sum_equals_100"],
            "PASS": pf,
            "FAIL": ff,
            "SKIP": sk,
            "latency_ms_avg": summary.get("latency_ms_avg"),
            "routing_accuracy": summary.get("routing_accuracy"),
            "overall_pass_rate": summary.get("overall_pass_rate"),
            "safe_stop_new": safe_stop_new,
            "FAIL_to_PASS": flip_fp,
            "PASS_to_FAIL": flip_pf,
            "exposure_cases": len(exposure_hits),
            "out": str(OUT),
        },
        ensure_ascii=False,
        indent=2,
    )
)
