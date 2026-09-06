# Design / changes (Codex remaining_failures handoff)

## Goal
Close residual Gold fails after polish **80%** without touching Excel / gold evaluator / prior result folders, without Test-id hardcoding, HyperCLOVA only.

## Code changes (freeze-matched, drift=0)
1. `chatbot/answer_contract.py` — fact slots, targeted retrieval enrichment, tax-rule vs tax-calc split, product-rule vs catalog listing, premise-check wording helpers, composition instructions.
2. `chatbot/query_router.py` — route tax-rule / product-rule questions away from wrong families.
3. `chatbot/agent_core.py` — wire `enrich_collection` + `composition_instruction`.
4. `chatbot/product_entity_precision.py` — audit helper for generic financial nouns (Gold scoring unchanged).
5. `chatbot/task_intent.py` — small intent tweak.
6. `tests/test_remaining_failures.py` — unit coverage for the above.

## Explicit non-goals / safeguards
- Do not invent missing operational numbers (교체매매 batch times, VPC reserved IPs) when evidence absent.
- Do not force mechanical `아닙니다` prefixes that harm premise checks.
- Product backend in this environment: **standard_json** (Postgres not connected; see `database_availability.json`).
- Test 11-style generic noun + particle hits remain evaluator precision issues; audited separately, scoring not loosened.

## Regression
- Unit: `tests/test_remaining_failures.py` → 7 passed
- Live T001–T022 post-change: **22/22 PASS** (`post_regression/`)
- Code freeze drift after Gold run1: **0**

## Gold-100
- Official: run1 in `gold100/` → **88/100 PASS**
- Codex started `--repeat 2`; process died mid run2 (~Test 9). Primary score is run1 (matches original brief “1회”).
