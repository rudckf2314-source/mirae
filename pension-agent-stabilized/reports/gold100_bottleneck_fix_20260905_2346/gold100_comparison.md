# Gold-100 Bottleneck-Fix Comparison

- baseline first: `c:\mirae\reports\gold100_hyperclova_20260905` (46 PASS / 54 FAIL)
- previous router/planner: `c:\mirae\reports\gold100_router_planner_20260905_2306` (60 PASS / 40 FAIL)
- this run: `c:\mirae\reports\gold100_bottleneck_fix_20260905_2346` (67 PASS / 33 FAIL)
- single re-run only (no cherry-picking)

## Overall

| metric | baseline | prev (router) | this run |
|---|---:|---:|---:|
| PASS | 46 | 60 | 67 |
| FAIL | 54 | 40 | 33 |
| PASS rate | 46.0%% | 60.0% | 67.0% |
| routing accuracy | 71.0% | 90.0% | 92.0% |
| required fact coverage avg | 0.2921 | 0.4839 | 0.4679 |
| legal/calculation accuracy | 20.0% | 26.7% | 33.3% |
| latency avg ms | 8363.7 | 8638.4 | 8573.6 |
| hallucination_cases (auto) | 0 | 1 | 1 |

- PASS delta vs prev: +7
- PASS delta vs first baseline: +21

## FAIL→PASS (vs prev)

- Test 1
- Test 2
- Test 6
- Test 9
- Test 10
- Test 31
- Test 53

## PASS→FAIL (vs prev)

- (none)

## Response status counts (this run)

{"success": 81, "clarify": 8, "safe_stop": 10, "system_error": 1}

## Remaining FAIL reason prefixes

- 11: D:safe_stop_for_required_answer
- 10: D:safe_stop_forbidden
- 9: D:correction_missing
- 8: R:route_family_mismatch
- 6: D:required_fact_coverage_low
- 6: D:safe_stop_despite_matching_db_rows
- 5: C:postgres_not_used_for_product_fact
- 1: X:exception
- 1: G:hallucination_invented_product

## Key bottleneck cases

- Test 1: PASS route=document+calculation status=success reasons=[]
- Test 2: PASS route=calculation status=success reasons=[]
- Test 6: PASS route=document status=success reasons=[]
- Test 7: FAIL route=document status=success reasons=['D:required_fact_coverage_low:1/3']
- Test 15: FAIL route=product status=safe_stop reasons=['D:safe_stop_for_required_answer', 'D:safe_stop_forbidden']
- Test 16: FAIL route=document status=success reasons=['R:route_family_mismatch:actual=document']
- Test 29: FAIL route= status=system_error reasons=["X:exception:AttributeError: 'NoneType' object has no attribute 'route'", 'D:safe_stop_for_required_answer', 'C:postgres_not_used_for_product_fact']
- Test 31: PASS route=calculation status=success reasons=[]
- Test 42: FAIL route=document status=success reasons=['G:hallucination_invented_product:미래에셋아세안셀렉트Q연금저축증권전환형자투자신탁1호(주식)', 'R:route_family_mismatch:actual=document', 'C:postgres_not_used_for_product_fact']
- Test 53: PASS route=document status=success reasons=[]
- Test 73: FAIL route=document status=success reasons=['D:correction_missing']

## Notes

- Do not treat auto hallucination_cases=1 as proof of zero hallucination.
- Excel / gold evaluator / prior report folders were not modified.
