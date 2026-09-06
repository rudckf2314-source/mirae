# Gold-100 Retest Comparison Report

- baseline: `c:\mirae\reports\gold100_hyperclova_20260905` (46 PASS / 54 FAIL / 0 SKIP, ~8.36s)
- retest: `c:\mirae\reports\gold100_retest_20260905_2217`
- started: 2026-09-05T22:17:48+0900
- finished: 2026-09-05T22:30:31+0900
- models: answer=HCX-005, supervisor=HCX-007, normalizer=HCX-DASH-002, extraction=HCX-005

## Integrity

- rows=100 unique=100 duplicates=none missing=none
- PASS/FAIL/SKIP = 46/54/0 (sum_ok=True)

## Overall

| metric | baseline | retest |
|---|---:|---:|
| PASS | 46 | 46 |
| FAIL | 54 | 54 |
| SKIP | 0 | 0 |
| overall PASS rate | 46.0% | 46.0% |
| routing accuracy | 71.0% | 71.0% |
| latency avg (ms) | 8363.7 | 7068.4 |
| safe_stop count | 13 | 13 |

- PASS delta: +0

## FAIL → PASS

- Test 30

## PASS → FAIL

- Test 73

## Interpretation notes

- Do **not** treat a single re-run delta as confirmed capability improvement.
- Natural-language display polish can change surface text without changing retrieval/routing substance.
- Content-level flips (FAIL↔PASS) should be reviewed case-by-case against expected answers.
- User-facing code-exposure cases (report only): 0 — verdicts unchanged.

