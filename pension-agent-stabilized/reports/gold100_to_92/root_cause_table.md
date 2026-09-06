# Remaining 11 FAIL — root-cause table (pre-fix)

Source run: `reports/gold100_test7_fix_20260906` (89/100).

Taxonomy: A true hallucination · B detector FP · C fact coverage · D structured-product contract · E correction · F route mismatch · G safe-stop · H unsupported/OOS · I evaluator labeling

| Test | Q summary | Expected | Route / source | Answer summary | Failure | Root cause | Agent / Eval / Unsupported | Fix? |
|---|---|---|---|---|---|---|---|---|
| 11 | 흥국 모자형 합성총보수 C-e | 원리 설명; 수치 없으면 확인불가 | product / R2 prospectus | 모투자신탁에서… + 연차별 % 제시 | G:invented `모투자신탁에서` | **B** entity FP (generic+particle). Number truth separate (prospectus may hold fees). | Agent wording + detector precision | Yes (P1) |
| 44 | 포괄 자동재예치 지시 가능? | 불가 + **2023-07-12** 시행 | document / FAQ | 불가 맞음, **날짜 누락** | D:fact 0/2 | **C** date slot not preserved in composition | Agent | Yes (P2) |
| 49 | 과기공 교체매매 실시간? | 비실시간 + **09:20** + **D+1** | document | 비실시간만, 시각/D+N 없음 | D:fact 0/3 | **C** (+**H** if times absent from corpus — `9시 20분` chunk miss) | Agent if evidence exists; else unsupported | Partial |
| 69 | 채권형 장외파생/사모 % | 10% / 5% | product+law → **catalog list** | 상품 5개 나열, 한도 없음 | D:fact 0/2 | **C**+**D** rule question treated as catalog search | Agent | Yes (P2/P3) |
| 70 | VPC 예약 IP | .0/.1/.2/.3/.255 | document | 자료 없음 고지 | D:fact 0/4 | **H** VPC evidence **not in corpus** (EXTENDED_PLATFORM) | Unsupported / corpus gap | No invent |
| 76 | 성향만료·하향·상품위험상향 vs 자동/직접매수 | 절차 대조 | **document** | 내용 대체로 맞음 | R:route + C:postgres | **F**+**I** procedure≠product catalog; backend is standard_json but flag says postgres | Agent metadata/route minimal; eval naming note | Yes minimal (P5) |
| 88 | IRP 8000만 전액 담보? | 불가, 최대 **50%**=4000만 | document+law | 압류금지로만 설명, 50% 없음 | D:fact 0/2 | **C** collateral-cap fact miss / 압류 conflation | Agent | Yes if evidence found (P2) |
| 94 | 소비자관련주 80% 사후이탈 | 기매입 예외로 위반 아님 | document | 규칙 맞음 + 특정 펀드명 언급 | G:invented `증권모투자신탁(주식)의` | **B** regex mid-name / generic+class FP | Detector precision | Yes (P1) |
| 95 | 아세안셀렉트Q 아시아전체+MSCI World? | 틀림; ASEAN + **MSCI South East Asia** | **document** | 부정만, 정확한 BM 누락 | R:route + C:postgres | **F**+**D**+**C** named product BM needs structured/product path | Agent | Yes (P3/P5) |
| 96 | 장내/장외/사모 한도 둘다 100%? | 100% / **10%** / **5%** | document | 잘못 **40%/30%** | C:postgres (+ content error) | **A**(wrong numbers)+**C**+**D** | Agent | Yes (P2/P3) |
| 97 | /24 전부 VM 할당? | 아님, 5개 예약 | document | **네** (전제 수용) | D:correction_missing | **E**+**H** no VPC evidence → false affirmation | Agent premise when evidence missing → clarify/insufficient, not yes | Yes (P4) careful |

## VPC / non-pension (Tests 70, 97)

| ID | Class |
|---|---|
| 70, 97 | **EXTENDED_PLATFORM** — not in current Enterprise RAG corpus; do **not** remove from Gold; do **not** invent IPs |

## Hallucination 2 prelim

| Test | Flag | Prelabel |
|---|---|---|
| 11 | `모투자신탁에서` | **FALSE_POSITIVE_ENTITY_DETECTION** (generic 모투자신탁 + particle). Separately audit fee numbers vs prospectus. |
| 94 | `증권모투자신탁(주식)의` | **FALSE_POSITIVE_ENTITY_DETECTION** (stem/class fragment from longer name) |

## Structured product / postgres label

Runtime backend = **standard_json**. Evaluator `postgres_not_used_for_product_fact` / `postgres_used` means “authoritative structured product path used” (route/source includes product). **Naming mismatch (I)**, not fixed by loosening evaluator this phase — set agent metadata `source_type=structured_product`, `backend=standard_json`, `product_lookup_used` and ensure product path when required.
