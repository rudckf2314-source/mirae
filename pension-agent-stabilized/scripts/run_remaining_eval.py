"""Gold runner with sidecar diagnostics; original evaluator remains untouched."""
import json
import sys
from pathlib import Path
from tests.gold100 import run_gold100

out = Path(sys.argv[sys.argv.index("--out-dir") + 1])
out.mkdir(parents=True, exist_ok=True)
original = run_gold100.EvalLangGraphAgent.respond

def traced(self, question, session, question_id):
    response = original(self, question, session, question_id)
    state = self.last_state or {}
    record = {"question_id": question_id, "question": question, "response": response,
              "route": state.get("route"), "safe_stop_reason": state.get("safe_stop_reason"),
              "verification_report": state.get("verification_report"),
              "evidence_coverage_report": state.get("evidence_coverage_report"),
              "calculation_result": state.get("calculation_result"),
              "claim_grounding_report": state.get("claim_grounding_report"),
              "product_backend": getattr(getattr(self.agent.legacy_agent, "product_db", None), "backend", None)}
    with (out / "response_diagnostics.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return response

run_gold100.EvalLangGraphAgent.respond = traced
run_gold100.main()
