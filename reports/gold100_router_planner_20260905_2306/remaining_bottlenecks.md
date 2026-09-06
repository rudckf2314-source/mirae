# Remaining bottlenecks after single Gold-100 re-run

## Headline
- **60 PASS / 40 FAIL (60.0%)** — improved from baseline 46%, still below the aspirational 65–75% band.
- This folder is the **only** post-change Gold run (no cherry-picking).

## High-impact residual FAIL modes
1. **`D:correction_missing`** — many legal/document narratives still lack evaluator-token correction phrasing even when content is directionally right (e.g. Test 2 answered 1,188,000원 / 13.2% but missed “아닙니다” token before a late phrasing fix; **Gold not re-run after that fix**).
2. **`R:route_family_mismatch`** — e.g. Test 1 correctly answers contribution + credit limits via `calculation`, while adapter expects another family; content improved, label mismatch remains.
3. **`D:required_fact_coverage_low`** — answers cite evidence but coverage ratio vs required slots stays low (Test 7).
4. **`safe_stop` (13)** — unchanged count vs baseline; includes ACTION_NOT_ALLOWED (orders) and residual evidence gaps (Test 6/15-style).
5. **Enterprise document gaps** — specialized ops (codes, VPC, edge transfer rules) still incomplete for full required-fact sets.

## PASS→FAIL (4) — treat as regressions to investigate, not score noise
- Test 29, 42, 53, 73 — see `gold100_comparison.md`; do not assume evaluator error without evidence review.

## Hallucination / exposure
- User-facing internal code exposure: **0**
- Automated `hallucination_cases`: **1** — **not** proof of zero hallucination; changed calc/legal PASS/FAIL flips need human evidence review.

## Post-Gold microfixes (not reflected in this run’s scores)
- Compound holding: force document/clarify even when weak catalog tokens (e.g. 국공채) match.
- Premise correction opener: lead with “아닙니다.” for clearer correction signal.
