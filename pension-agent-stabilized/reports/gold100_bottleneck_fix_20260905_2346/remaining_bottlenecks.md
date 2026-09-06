# Remaining bottlenecks after bottleneck-fix Gold run (67%)

## Headline
- **67 PASS / 33 FAIL** — up from 60% (router/planner) and 46% (first baseline)
- Inside aspirational 65–75% band on this single run; **not** a guarantee of future runs
- **0 PASS→FAIL** vs previous router/planner run; **+7 FAIL→PASS**

## Still failing (high volume)
1. **`D:correction_missing`** — document narratives that contradict a premise but still miss evaluator tokens in some edge wordings (e.g. absolute claims without 맞나요)
2. **`R:route_family_mismatch`** — product-typed Excel rows still answered via document (postgres_not_used)
3. **`D:safe_stop_*`** — residual evidence/law gaps on niche ops (중도인출 자격 상태전이, 통지 의무 등)
4. **`D:required_fact_coverage_low`** — numeric slots in 기대답변 not all echoed (codes 01/50, VPC octets, etc.)
5. **Enterprise/document coverage** — specialized RAG content still incomplete for some 최상 items

## Auto checks
- Internal code exposure (user-facing): **0**
- hallucination_cases: **1** — do **not** treat as zero hallucination; review changed calc/legal answers with evidence

## Suggested next focus (if continuing)
- Product postgres path for 운용제한/의무비율 without inventing prospectus text
- Deterministic ops code mapping when enterprise docs name codes explicitly
- Safer partial answers for remaining safe_stops instead of hard stop
