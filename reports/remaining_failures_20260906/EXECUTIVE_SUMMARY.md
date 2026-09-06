# Remaining failures run — executive summary

**Verdict: Gold-100 PASS 88/100 (was polish 80, baseline 46).**

| Stage | PASS |
|---|---|
| baseline `gold100_hyperclova_20260905` | 46 |
| polish `gold100_bottleneck_polish_20260906_0043` | 80 |
| remaining_failures run1 | **88** |

## What improved
- vs polish: **+8** cases
- FAIL→PASS: Test 18, Test 73, Test 80, Test 81, Test 83, Test 90, Test 91, Test 92, Test 100
- PASS→FAIL regressions: Test 7

## Residual fails (12)
- Test 7 [B]: `D:required_fact_coverage_low:1/3`
- Test 11 [D]: `G:hallucination_invented_product:모투자신탁에서`
- Test 44 [B]: `D:required_fact_coverage_low:0/2`
- Test 49 [B]: `D:required_fact_coverage_low:0/3`
- Test 69 [C]: `D:required_fact_coverage_low:0/2`
- Test 70 [F]: `D:required_fact_coverage_low:0/4`
- Test 76 [A]: `R:route_family_mismatch:actual=document; C:postgres_not_used_for_product_fact`
- Test 88 [B]: `D:required_fact_coverage_low:0/2`
- Test 94 [D]: `G:hallucination_invented_product:증권모투자신탁(주식)의`
- Test 95 [C]: `R:route_family_mismatch:actual=document; C:postgres_not_used_for_product_fact`
- Test 96 [C]: `C:postgres_not_used_for_product_fact`
- Test 97 [B]: `D:correction_missing`

## Caveats
- Product backend observed: **standard_json** (Postgres connection timeout). Do not claim live Postgres verification.
- Auto `hallucination_cases` is not proof of zero hallucination; Test 11 / 94 remain invented-product flags (generic noun particle / named fund wording).
- Content review still needed for calc/legal flips — score alone is insufficient.
- Reproducibility second Gold run started by Codex but process exited mid-run; not used as official score.
