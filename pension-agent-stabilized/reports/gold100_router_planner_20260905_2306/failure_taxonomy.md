# Failure Taxonomy (pre-fix diagnosis)

Sources:
- `reports/gold100_hyperclova_20260905` (first baseline)
- `reports/gold100_retest_20260905_2217` (NL-display retest)

Both runs: **46 PASS / 54 FAIL**, routing **71%**, legal/calc **20%**.

## Bucket summary (retest 54 fails)

| Bucket | Count (approx) | Primary signal |
|--------|----------------|----------------|
| A. Agent failure | ~40 | wrong route to product catalog, incomplete tax calc, wrong safe_stop message |
| B. Evaluator / label friction | ~8 | `R:route_family_mismatch` when adapter route families are narrower than valid routes |
| C. Missing customer info / scope | ~6 | holding product unnamed, order execution requests |

Many items have A as primary and B as secondary (adapter expects document while agent wrongly chose product).

## Common Agent failures (fixed in this change)

1. **Product-hint pollution**: class names containing `퇴직연금` made almost any retirement question `named_product` → product catalog dump.
2. **Task intent ignored**: `실물이전`, `가입자 교육`, `최소적립` still fell through to product via noun `상품/펀드`.
3. **Tax amount parse**: `6,000만 원` failed; contribution vs salary roles collapsed → limit_summary only.
4. **Tax answer incomplete**: annual contribution limit 18,000,000 missing; rates shown without local surcharge; no premise correction.
5. **Orders / holding**: buy-order requests and unnamed holdings not separated into ACTION_NOT_ALLOWED / NEEDS_CLARIFICATION.

## Sample classifications

| test_id | Primary | Secondary | Notes |
|---------|---------|-----------|-------|
| Test 1 | A | B | Calc OK path but missed 납입한도 1800만 |
| Test 2 | A | — | Salary/contribution parse + correction missing |
| Test 4/7/12 | A | B | Procedure routed to product |
| Test 6 | C | A | Holding TDF unnamed → should clarify |
| Test 15 | C | — | Buy order out of scope |
| Test 24/25/26 | A | B | Education/DB funding rules → product dump |
| Test 16–18 | B/A | — | Tax narrative may be document-valid; adapter prefers calculation |

Original Excel, gold evaluator, and prior result folders were **not** modified.
