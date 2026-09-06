# Gold100 → 92~95 attempt — completion report

## 1. 작업 요약
잔여 11 FAIL root-cause 표를 먼저 작성한 뒤, hallucination FP 정밀화 · fact slot · structured product metadata · 최소 라우팅을 적용했다.  
**목표 92~95는 미달.** Hotfix 후 재현성 2회 결과는 **run1 88 / run2 89**. Hallucination flag는 **0**으로 내려갔다. Fact coverage는 **하락**(0.59 → ~0.47–0.49).

## 2. 수정 파일
- `chatbot/product_entity_precision.py` — 일반 금융명사(+조사·(주식|채권)) precision
- `tests/agent_eval/evaluators.py` — `invented_products`에 precision 필터 연결 (threshold 미완화)
- `chatbot/answer_contract.py` — 한도/담보/벤치마크 슬롯, preserved facts, compact composition, VPC premise 가드
- `chatbot/query_router.py` — investment-limit → document+product, brand+셀렉트 이름 인식
- `chatbot/pension_protocol.py` — `source_type` / `backend=standard_json` / `product_lookup_used` metadata
- `tests/test_remaining_failures.py` — 관련 유닛 테스트

## 3. 미수정 architecture
LangGraph / HyperCLOVA / Competition Mode / Enterprise RAG / standard_json / Legal / Rule Engine / Claim Grounding / Response Guard 유지. Evaluator 기준·Excel·기대답변 미변경.

## 4. 잔여 11 FAIL root-cause table
사전 표: `reports/gold100_to_92/root_cause_table.md`  
(원 89 기준 11건 분류 A–I 완료 후 수정 진행)

## 5. Hallucination 2건 판정
| Test | 판정 | 결과 |
|---|---|---|
| 11 `모투자신탁에서` | **FALSE_POSITIVE_ENTITY_DETECTION** | detector precision 수정 후 run2에서 PASS, hallu flag 없음 |
| 94 `증권모투자신탁(주식)의` | **FALSE_POSITIVE_ENTITY_DETECTION** | run1/run2 PASS |

True hallucination = **0** (auto flag).  
다른 98건에 대한 false-negative 완화(threshold 인하)는 하지 않음.

## 6. Fact coverage 개선 내용
슬롯·targeted retrieval·숫자 preserve를 넣었으나 **평균 coverage는 개선되지 않음**  
- prev 89: **0.588**  
- hotfix run1: **0.493**  
- hotfix run2: **0.473**  
목표 0.70 미달. 44/49/88 등 핵심 수치 누락 지속. 49의 09:20·VPC는 corpus 부재 가능.

## 7. Structured product source contract
Runtime metadata에 `structured_product_source=standard_json`, `product_lookup_used` 추가.  
Evaluator의 `postgres_*` 라벨은 **이름 불일치(I)** — 점수 올리려 evaluator 미수정.  
`document+product` 라우트가 `both`로 표기되며 adapter `require_postgres`와 어긋나 29/69/96 등에서 system_error·route mismatch가 남음.

## 8. Correction 개선
VPC 무근거 긍정 방지 힌트 추가. Test 97은 여전히 `correction_missing` (근거 부재 + 전제 처리 미완).

## 9. Route 최소 수정
investment-limit / brand+benchmark만 최소 조정. 전체 router 재작성 없음.  
부작용: `both` 라벨·product 병행이 일부 케이스에서 guard/system_error 유발.

## 10. Unit tests
`tests/test_remaining_failures.py` → **11 passed**

## 11. T001–T022
hotfix 후 `reports/gold100_to_92/regression_hotfix` → **22/22 PASS**

## 12. Gold run1 (공식 blind)
`reports/gold100_to_92/gold100_hotfix/`  
- **PASS 88/100**  
- routing 96% · fact 0.493 · hallu **0** · legal/calc 관련 summary 참조  
- success/clarify/system_error: **86 / 10 / 4**

## 13. Gold run2
같은 폴더 `gold100_run_2_results.json`  
- **PASS 89/100**  
- routing 96% · fact 0.473 · hallu **0**  
- success/clarify/system_error: **87 / 10 / 3**  
- 프로세스 정상 종료 (중도 종료 없음)

## 14. run1/run2 flips
| | |
|---|---|
| FAIL→PASS | **Test 11** only |
| PASS→FAIL | **none** |
| route flips | 0 |

## 15. 점수 궤적
| Stage | PASS | hallu | fact |
|---|---|---|---|
| baseline 46 | 46 | — | — |
| … → polish 80 | 80 | — | — |
| remaining 88 | 88 | 2 | 0.546 |
| Test7 fix **89** | **89** | 2 | **0.588** |
| to92 1차 시도 | 84 / 82 | 0 | ~0.53 |
| hotfix run1 | **88** | **0** | 0.493 |
| hotfix run2 | **89** | **0** | 0.473 |

## 16. 신규 PASS→FAIL (vs 공식 89)
hotfix run2 기준 DOWN: **Test 12, Test 29, Test 30**  
UP: **Test 11, Test 94, Test 95**  
순효과 ≈ 0 (89 유지), 목표 92+ 미달.

## 17. Known limitations
- 92~95 미달; fact coverage 악화
- `both` route + postgres 라벨 불일치로 29/69/96 system_error
- VPC 70/97 · 교체매매 시각 49 · IRP 담보 50% 88 — corpus/법령 한도 미회수
- Test 95는 PASS이나 catalog 나열 품질 이슈 가능 (내용 재검토 필요)
- evaluator 완화·test_id 하드코딩 없음

## 18. Holdout 진행 가능 여부
**불가 (지금 freeze 비권장).**  
사유: 목표 92~95 미달, fact coverage 후퇴, 신규 회귀(12/29/30) 존재.  
Holdout 전에 `both`/postgres contract와 safe_stop(system_error) 원인을 안정화한 뒤 89→92+ 재도전 필요.

### 산출물 경로
- Root-cause: `reports/gold100_to_92/root_cause_table.md`
- Hotfix Gold: `reports/gold100_to_92/gold100_hotfix/`
- 중간 실패 시도(참고): `reports/gold100_to_92/gold100/` (84)
