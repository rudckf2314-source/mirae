# Regression results (bottleneck fix)

## Unit
- `TEST/test_router_planner_calc.py` + `TEST/test_legal_db_v4.py`
- **17 passed**

## T001–T022
- Output: `reports/bottleneck_fix_regression`
- Result: **22/22 PASS** (average ~9.8 after Literal route fix)
- Intermediate slip: T017/T022 failed once on `document+calculation` not in SpecificationBundle Literal — fixed before Gold

## Gold-100
- Output: `reports/gold100_bottleneck_fix_20260905_2346`
- **67 PASS / 33 FAIL (67.0%)** — single run, no cherry-pick
