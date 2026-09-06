# V7 Stable Hybrid Architecture

목표는 V6의 BM25/TF-IDF/RRF, adaptive query, evidence coverage, claim grounding을 버리는 것이 아니라 **도메인별 authoritative lane을 먼저 보존한 뒤 연구형 검색을 보조 계층으로 제한**하는 것이다.

## 핵심 원칙

1. Conversation lane은 retrieval보다 먼저 실행한다. 세션 follow-up은 새 독립 질의로 재분류하지 않는다.
2. Product lane에서 상품 identity와 정형 수치는 Product DB가 authoritative source다. BM25/RRF는 상품 identity를 대체하지 않는다.
3. Document lane에서 BM25 + char/word TF-IDF + RRF + adaptive multi-query를 적극 사용한다.
4. Legal/Tax lane은 Enterprise RAG + Legal DB + deterministic Rule Engine을 결합한다.
5. Evidence Coverage는 source-aware하게 사용한다. SQL로 완결되는 정형 fact에 불필요한 document evidence를 강제하지 않는다.
6. Claim Grounding과 Response Guard는 final answer에만 적용하고 내부 planning text는 사용자에게 노출하지 않는다.

## 실행 순서

User
→ Input Contract
→ Conversation Resolver / Pending Task Resume
→ Intent + Domain Plan
→ authoritative lane selection
   - Product Entity Resolver → Product DB → optional PDF/RAG
   - Legal Guardrail → Legal DB → Rule Engine + Enterprise RAG
   - Document Hybrid RAG → BM25/TF-IDF/RRF
→ Evidence Hub
→ Source-aware Coverage
→ Rule Verification
→ HCX Answer Composer
→ Claim Grounding
→ Response Guard
→ Final

## V6 대비 안정화 포인트

- SessionContext의 pending_task 계약을 명시적으로 허용해 follow-up input_error를 제거한다.
- Standard JSON의 실제 소문자 fee_type과 performance.metric 필드를 정상 해석한다.
- TDF는 제공 카탈로그의 라이프사이클 상품 alias로 deterministic하게 제한한다.
- Named/family product는 RAG lexical ranking 이전에 catalog resolver로 고정한다.
- 세액공제 계산은 Legal DB 숫자를 유지하면서 관련 Enterprise 문서도 함께 검색한다.
