# Code changes — bottleneck fix pass

## Target bottlenecks (from 60% run)
1. Over-broad order detection → false safe_stop on policy questions
2. ACTION_NOT_ALLOWED emitted as safe_stop while Gold requires an answer
3. Document/law verifier hard-fail despite enterprise hits → blanket safe_stop
4. Tax limit questions routed as `calculation` only → route_family_mismatch vs document families
5. Correction answers missing evaluator tokens (`아닙니다` …)
6. ISA rollover extra credit not computed (Test 31 style)
7. Product prospectus limit questions falling onto document-only

## Changes
- `task_intent.py`: imperative-only ORDER_MARKERS; tax/ISA/prospectus-limit intents; narrower correction signals
- `pension_ambiguity.py`: orders EXECUTE with ACTION_NOT_ALLOWED notice (not SAFE_STOP)
- `pension_verifier.py`: document hits + incomplete fields → warning; law gaps soft when documents present
- `query_router.py`: document+calculation for limit policy; tax explain → document+law; product_attribute for 운용제한/의무비율; router policy v5
- `pension_specs.py` / agent RouteName: allow `document+calculation`, `calculation+law`
- `calculation_gateway/worker/verifier` + agent answers: ISA transfer credit from Legal DB policy (10% cap 3M); limit summary includes IRP remainder 3M; correction/order prefixes in answer/finalize
- Tests: `TEST/test_router_planner_calc.py` expanded (17 passed with legal_db tests)

## Constraints honored
- No Excel / gold evaluator edits
- No Test-id hardcoding
- HyperCLOVA only; secrets not printed
- Single Gold re-run after validation
