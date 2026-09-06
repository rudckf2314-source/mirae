# Regression suite results

## Pre-change
- Command: `tests.agent_eval.run_eval --ids T001..T022`
- Output: `reports/router_planner_fix_precheck`
- Result: **22/22 PASS**, average 9.79

## Post-change
- Output: `reports/router_planner_fix_postcheck`
- Result: **22/22 PASS**, average 9.79

## New unit tests
- `TEST/test_router_planner_calc.py`
- Coverage: procedure≠product, amount parse, tax salary/contribution split, effective rate, order/holding intents
- Result: **14 passed** (with `TEST/test_legal_db_v4.py`)

No evaluator loosening. No Excel edits.
