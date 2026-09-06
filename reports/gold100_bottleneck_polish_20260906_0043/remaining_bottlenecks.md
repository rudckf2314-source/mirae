# Remaining bottlenecks (80% run)

## Headline
- **80 PASS / 20 FAIL** — up from 71% / 67% / 60% / 46%
- Above prior 65–75% aspiration on this single run; not a guarantee for future runs

## Dominant residual fails
1. **`D:correction_missing`** — some false-premise cases still lack evaluator tokens
2. **`D:required_fact_coverage_low`** — numeric slots (dates/times/codes) not fully echoed
3. **`R:route_family_mismatch`** — occasional family mismatch remains
4. **Rare calculation safe_stop** (e.g. Test 90)

## Auto checks
- Code exposure: see report
- hallucination_cases: **1** — do not treat as zero hallucination
