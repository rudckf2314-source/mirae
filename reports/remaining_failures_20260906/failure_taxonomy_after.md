# Failure taxonomy after remaining_failures changes

Buckets: A semantic · B fact completeness · C product orchestration · D evaluator precision · E stop policy · F missing context/scope

PASS **88/100**. Failures: **12**.

## Test 7 · bucket B
- family/route/status: Procedure / document / success
- reasons: ['D:required_fact_coverage_low:1/3']

## Test 11 · bucket D
- family/route/status: Product / product / success
- reasons: ['G:hallucination_invented_product:모투자신탁에서']

## Test 44 · bucket B
- family/route/status: Document / document / success
- reasons: ['D:required_fact_coverage_low:0/2']

## Test 49 · bucket B
- family/route/status: Document / document / success
- reasons: ['D:required_fact_coverage_low:0/3']

## Test 69 · bucket C
- family/route/status: Product / product+law / success
- reasons: ['D:required_fact_coverage_low:0/2']

## Test 70 · bucket F
- family/route/status: Infrastructure / document / success
- reasons: ['D:required_fact_coverage_low:0/4']

## Test 76 · bucket A
- family/route/status: Product / document / success
- reasons: ['R:route_family_mismatch:actual=document', 'C:postgres_not_used_for_product_fact']

## Test 88 · bucket B
- family/route/status: Legal / document+law / success
- reasons: ['D:required_fact_coverage_low:0/2']

## Test 94 · bucket D
- family/route/status: Other / document / success
- reasons: ['G:hallucination_invented_product:증권모투자신탁(주식)의']

## Test 95 · bucket C
- family/route/status: Product / document / success
- reasons: ['R:route_family_mismatch:actual=document', 'C:postgres_not_used_for_product_fact']

## Test 96 · bucket C
- family/route/status: Product / document / success
- reasons: ['C:postgres_not_used_for_product_fact']

## Test 97 · bucket B
- family/route/status: Infrastructure / document / success
- reasons: ['D:correction_missing']
