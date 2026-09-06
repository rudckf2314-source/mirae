# Gold-100 Bottleneck-Continue Comparison

- first baseline: `c:\mirae\reports\gold100_hyperclova_20260905` (46/54)
- previous: `c:\mirae\reports\gold100_bottleneck_fix_20260905_2346` (67/33)
- this run: `c:\mirae\reports\gold100_bottleneck_continue_20260906_0013` (71/29)
- single run, no cherry-picking

## Overall

| metric | baseline | prev | this |
|---|---:|---:|---:|
| PASS | 46 | 67 | 71 |
| FAIL | 54 | 33 | 29 |
| PASS rate | 46.0% | 67.0% | 71.0% |
| routing | 71.0% | 92.0% | 94.0% |
| fact coverage | 0.2921 | 0.4679 | 0.4431 |
| legal/calc | 20.0% | 33.3% | 46.7% |
| hallucination_cases | 0 | 1 | 0 |

- delta vs prev: +4 PASS
- delta vs baseline: +25 PASS

## FAIL→PASS
- Test 7
- Test 15
- Test 16
- Test 17
- Test 29
- Test 42

## PASS→FAIL
- Test 9
- Test 94

## Status counts
{"success": 80, "clarify": 10, "system_error": 9, "safe_stop": 1}

## Remaining FAIL reasons
- 10: D:safe_stop_for_required_answer
- 10: D:correction_missing
- 6: R:route_family_mismatch
- 5: D:required_fact_coverage_low
- 2: C:postgres_not_used_for_product_fact
- 1: D:safe_stop_forbidden
- 1: D:safe_stop_despite_matching_db_rows

## Key cases
- Test 7: PASS route=document status=success []
- Test 15: PASS route=product status=success []
- Test 16: PASS route=document+law status=clarify []
- Test 17: PASS route=document+law status=clarify []
- Test 18: FAIL route=document status=success ['R:route_family_mismatch:actual=document']
- Test 19: FAIL route=document+law status=system_error ['D:safe_stop_for_required_answer']
- Test 29: PASS route=product status=success []
- Test 42: PASS route=product status=success []
- Test 53: PASS route=document status=success []

## Notes
- Auto hallucination_cases=0 is not proof of zero hallucination.
- Excel/evaluator/prior folders unchanged.
