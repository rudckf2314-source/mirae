# Gold-100 Bottleneck-Polish Comparison

- baseline: `c:\mirae\reports\gold100_hyperclova_20260905` (46/54)
- previous: `c:\mirae\reports\gold100_bottleneck_continue_20260906_0013` (71/29)
- this run: `c:\mirae\reports\gold100_bottleneck_polish_20260906_0043` (80/20)
- single run; no cherry-picking; evaluator unchanged

## Overall

| metric | baseline | prev (71%) | this |
|---|---:|---:|---:|
| PASS | 46 | 71 | 80 |
| FAIL | 54 | 29 | 20 |
| PASS rate | 46.0% | 71.0% | 80.0% |
| routing | 71.0% | 94.0% | 95.0% |
| fact coverage | 0.2921 | 0.4431 | 0.5041 |
| legal/calc | 20.0% | 46.7% | 66.7% |
| hallucination_cases | 0 | 0 | 1 |

- delta vs prev: +9 PASS
- delta vs baseline: +34 PASS

## FAIL→PASS
- Test 9
- Test 19
- Test 22
- Test 40
- Test 62
- Test 63
- Test 65
- Test 87
- Test 89
- Test 99

## PASS→FAIL
- Test 11

## Status counts
{"success": 89, "clarify": 10, "safe_stop": 1}

## Remaining FAIL reasons
- 7: D:correction_missing
- 6: D:required_fact_coverage_low
- 5: R:route_family_mismatch
- 3: C:postgres_not_used_for_product_fact
- 1: G:hallucination_invented_product
- 1: D:safe_stop_for_required_answer
- 1: D:safe_stop_forbidden
- 1: D:safe_stop_despite_matching_db_rows

## Key cases
- Test 9: PASS route=document status=success []
- Test 18: FAIL route=document status=success ['R:route_family_mismatch:actual=document']
- Test 19: PASS route=document+law status=success []
- Test 22: PASS route=document+law status=success []
- Test 40: PASS route=document+law status=success []
- Test 62: PASS route=document+law status=success []
- Test 65: PASS route=document+law status=success []
- Test 87: PASS route=document+law status=success []
- Test 89: PASS route=document+law status=success []
- Test 99: PASS route=document status=success []

## Notes
- Auto hallucination_cases=1 is not proof of zero hallucination.
