# Comparison vs polish80 / baseline46

- remaining_failures Gold run1 PASS: **88/100 (88%)**
- polish_0043 PASS: **80/100**
- baseline_0905 PASS: **46/100**
- delta vs polish: **+8**
- delta vs baseline: **+42**

## Metrics snapshot (run1)

- pass: 88
- fail: 12
- overall_pass_rate: 88.0%
- routing_accuracy: 98.0%
- source_selection_accuracy: 95.1%
- required_fact_coverage_avg: 0.546
- product_retrieval_accuracy: 58.3%
- legal_calculation_accuracy: 93.3%
- hallucination_cases: 2
- latency_ms_avg: 10818.8
- latency_ms_p50: 11156.7
- latency_ms_max: 26720.8

## FAIL -> PASS (vs polish80)

- Test 18
- Test 73
- Test 80
- Test 81
- Test 83
- Test 90
- Test 91
- Test 92
- Test 100

## PASS -> FAIL (vs polish80)

- Test 7: ['D:required_fact_coverage_low:1/3']

## Still FAIL (vs polish80)

- Test 11: ['G:hallucination_invented_product:모투자신탁에서']
- Test 44: ['D:required_fact_coverage_low:0/2']
- Test 49: ['D:required_fact_coverage_low:0/3']
- Test 69: ['D:required_fact_coverage_low:0/2']
- Test 70: ['D:required_fact_coverage_low:0/4']
- Test 76: ['R:route_family_mismatch:actual=document', 'C:postgres_not_used_for_product_fact']
- Test 88: ['D:required_fact_coverage_low:0/2']
- Test 94: ['G:hallucination_invented_product:증권모투자신탁(주식)의']
- Test 95: ['R:route_family_mismatch:actual=document', 'C:postgres_not_used_for_product_fact']
- Test 96: ['C:postgres_not_used_for_product_fact']
- Test 97: ['D:correction_missing']

## Current FAIL list

- Test 7 [B]: ['D:required_fact_coverage_low:1/3']
- Test 11 [D]: ['G:hallucination_invented_product:모투자신탁에서']
- Test 44 [B]: ['D:required_fact_coverage_low:0/2']
- Test 49 [B]: ['D:required_fact_coverage_low:0/3']
- Test 69 [C]: ['D:required_fact_coverage_low:0/2']
- Test 70 [F]: ['D:required_fact_coverage_low:0/4']
- Test 76 [A]: ['R:route_family_mismatch:actual=document', 'C:postgres_not_used_for_product_fact']
- Test 88 [B]: ['D:required_fact_coverage_low:0/2']
- Test 94 [D]: ['G:hallucination_invented_product:증권모투자신탁(주식)의']
- Test 95 [C]: ['R:route_family_mismatch:actual=document', 'C:postgres_not_used_for_product_fact']
- Test 96 [C]: ['C:postgres_not_used_for_product_fact']
- Test 97 [B]: ['D:correction_missing']

Bucket counts: {'B': 5, 'D': 2, 'C': 3, 'F': 1, 'A': 1}

