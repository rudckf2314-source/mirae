# Code changes — bottleneck continue

## Focus (from 67% remaining fails)
1. ACTION_NOT_ALLOWED still scored as safe_stop (Test 15)
2. Institution marker `의무` stole prospectus-limit questions (Test 29/42)
3. Absolute-claim+가능한가 false correction (Test 19)
4. Tax explain (이월/분리과세) route family mismatches
5. Evidence coverage hard-stop when law missing but documents present
6. `NoneType.route` crash on product attribute (Test 29)
7. Unsupported `both` / missing route Literals

## Changes
- task_intent: remove bare `의무` from institution; prospectus limits before institution; tax before institution; narrower absolute-claim pairing
- query_router: product_attribute stays product-only; correction tax → document+law; router v6
- pension_protocol: ACTION_NOT_ALLOWED + answer → status success
- pension_langgraph_agent: coverage soft-continue; rule_bundle None-safe; ACTION_NOT_ALLOWED completes without safe_stop_reason; both/document+calculation route workers
- pension_specs: RouteName includes both / document+calculation / calculation+law
- tests expanded (20 unit passed); T001–T022 22/22

## Constraints
- No Excel/evaluator edits, no Test-id hardcoding, HyperCLOVA only, single Gold run
