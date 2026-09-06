# Gold-100 Retest Comparison Report

- baseline: `c:\mirae\reports\gold100_hyperclova_20260905` (46 PASS / 54 FAIL / 0 SKIP, ~8.36s)
- retest: `c:\mirae\reports\gold100_router_planner_20260905_2306`
- started: 2026-09-05T23:06:24+0900
- finished: 2026-09-05T23:21:44+0900
- models: answer=HCX-005, supervisor=HCX-007, normalizer=HCX-DASH-002, extraction=HCX-005

## Integrity

- rows=100 unique=100 duplicates=none missing=none
- PASS/FAIL/SKIP = 60/40/0 (sum_ok=True)

## Overall

| metric | baseline | retest |
|---|---:|---:|
| PASS | 46 | 60 |
| FAIL | 54 | 40 |
| SKIP | 0 | 0 |
| overall PASS rate | 46.0% | 60.0% |
| routing accuracy | 71.0% | 90.0% |
| latency avg (ms) | 8363.7 | 8638.4 |
| safe_stop count | 13 | 13 |

- PASS delta: +14

## FAIL ??PASS

- Test 4
- Test 12
- Test 13
- Test 24
- Test 25
- Test 26
- Test 30
- Test 33
- Test 35
- Test 36
- Test 38
- Test 51
- Test 55
- Test 64
- Test 67
- Test 86
- Test 93
- Test 94

## PASS ??FAIL

- Test 29
- Test 42
- Test 53
- Test 73

## Interpretation notes

- Do **not** treat a single re-run delta as confirmed capability improvement.
- Natural-language display polish can change surface text without changing retrieval/routing substance.
- Content-level flips (FAIL?봒ASS) should be reviewed case-by-case against expected answers.
- User-facing code-exposure cases (report only): 0 ??verdicts unchanged.

