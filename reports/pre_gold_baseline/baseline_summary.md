# Pre–Gold Set Baseline Summary

Frozen after quality-hardening (unit display, performance audit, semantic risk mapping, product-risk answers, observability).

## Live regression (executed)

| Metric | Run 1 | Run 2 |
|--------|-------|-------|
| Cases | 22/22 PASS | 22/22 PASS |
| Turns | 29 | 29 |
| Average score | 9.79 | 9.79 |
| Intent flips | 0 | — |
| Route flips | 0 | — |
| Candidate flips | 0 | — |
| Context flips | 0 | — |

- Entrypoint: `PensionLangGraphAgent.respond` / `POST /api/search`
- Provider: HyperCLOVA (`COMPETITION_MODE=1`)
- Artifacts: `live_22_results.json`, `run_2_results.json`, `reproducibility.md`

## Quality gates observed on live answers

- Internal unit enum leak (`PERCENT_PER_YEAR`, etc.): **0**
- Debug leak (`product_limit=`, PostgreSQL/Standard JSON): **0**
- Evaluator not loosened; PASS requires actual executed runs above

## Data quality snapshot

From `data_quality_audit.json` (461 product records):

- `fund_return` / `1Y`: 162 rows
- Status: VERIFIED 114 / SOURCE_CONFLICT 42 / UNVERIFIED 6
- T008 top-5 sample ranking prefers verified percent-scale rows; conflict fund-code magnitudes are not LLM-rescaled

## Architecture preserved

- LangGraph orchestration unchanged
- BM25 + char TF-IDF + word TF-IDF + RRF
- Adaptive query, Product PostgreSQL, Enterprise RAG
- Legal DB / Guardrail, Rule Engine, Claim Grounding, Response Guard
- `COMPETITION_MODE=1` default path

## Scope note

T001–T022 is a **regression suite**, not a claim of Gold Set generalization.
Next step: 80–150 Gold Question Set for real performance evaluation.
