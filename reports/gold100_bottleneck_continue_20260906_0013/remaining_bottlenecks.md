# Remaining bottlenecks (71% run)

## Headline
- **71 PASS / 29 FAIL** — up from 67% / 60% / 46%
- Inside 65–75% aspiration on this single run; not a guarantee

## Dominant residual fails
1. **`D:correction_missing`** — many document answers still lack evaluator correction tokens when Excel expects `아님…`
2. **`D:required_fact_coverage_low`** — numeric slots (codes, VPC, 교체매매 times) not all echoed
3. **`R:route_family_mismatch` + postgres** — some product-typed rows still answered via document
4. **Occasional `system_error` / calculation safe_stop** — guard or unsupported calc edge cases (e.g. Test 90)

## Auto checks
- Code exposure: see report (expected 0)
- hallucination_cases: **0** this run — still not proof of zero hallucination

## Next leverage (if continuing)
- Stronger premise-correction phrasing for procedure/legal false premises without hardcoding Test IDs
- Ops code/threshold fact extraction from enterprise hits (소규모 펀드 01 / 50억)
- Product postgres path completeness for prospectus obligation ratios
