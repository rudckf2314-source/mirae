# Test 7 regression recovery report

## 1. Root cause
- Retrieval did find `doc34.xlsx` (실물이전 불가사유 표).
- Primary row exists: `01. 소규모 펀드 임위해지 | … 잔고 50억 미만`.
- A later chunk also says `99.기타 : 불가사유 | 01. 소규모 … ~ 25. … 이 외에`.
- Final answer collapsed the catch-all parent **99** with the reason text, dropping leaf code **01**.
- Contributing: `is_product_rule_question` included `실물이전`, so enrichment/retrieval used `source_group=None` and mixed product PDFs into a procedure/code-mapping question.
- Not a fallback rewriter inventing 99; mapping/composition confusion between hierarchical table rows.

## 2. Files changed
- `chatbot/answer_contract.py`
- `tests/test_remaining_failures.py`

## 3. Fix (generalized, no test_id)
- Remove `실물이전` from product-rule classification (keep docs source_group for transfer procedures).
- Add transfer reject-code slots + `extract_reject_code_matches()` that parses primary `NN. reason | detail` rows and **skips 99 catch-all**.
- Inject matched leaf code into the composition contract; instruct model not to replace 01–25 rows with 99.

## 4. Unit tests
- `tests/test_remaining_failures.py`: **8 passed** (added primary-vs-99 mapping test)

## 5. T001–T022
- `reports/test7_fix_20260906/regression`: **22/22 PASS**

## 6. Gold-100 (1회)
- Out: `reports/gold100_test7_fix_20260906`
- PASS **89/100** (prev official run1 **88/100**)
- Smoke Test 7 only also PASS beforehand

## 7. Test 7
- **PASS** — answer cites `01. 소규모 펀드 임의해지`, 50억 미만, 환매/현금화 후 이전

## 8. New regressions vs 88 run1
- FAIL→PASS: Test 7 only
- PASS→FAIL: **none**
- Still FAIL (11): 11, 44, 49, 69, 70, 76, 88, 94, 95, 96, 97

### Metrics snapshot
| metric | prev 88 | now 89 |
|---|---|---|
| PASS | 88 | **89** |
| routing | 98.0% | 98.0% |
| fact coverage avg | 0.546 | **0.588** |
| hallucination_cases | 2 | 2 |
| legal/calc accuracy | 93.3% | 93.3% |
| response_status clarify | (prev) | 10 |
| response_status success | (prev) | 90 |
