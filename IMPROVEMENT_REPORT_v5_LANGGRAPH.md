# Improvement Report v5 — Adaptive Multi-Agent LangGraph

## 목적

미래에셋 연금 Agent 과제의 숨은 평가셋에 대비해, v4 Legal DB 기반 구조를 유지하면서 LangGraph에 **질의 복잡도 분석 → 전문 worker → 근거 완전성 검사 → 규칙 검증 → 답변 생성 → claim grounding** 계층을 추가했습니다.

## 신규 파일

- `chatbot/adaptive_query.py`: intent/entity/constraint/complexity 분석 및 복합질의 decomposition 힌트
- `chatbot/evidence_coverage.py`: 계획에 필요한 document/product/law/calculation 근거 누락 탐지
- `chatbot/claim_grounding.py`: 최종 답변의 숫자가 근거/계산 결과에 없는 경우 REVIEW 표시
- `chatbot/domain_registry.py`: 신규 금융 도메인을 core if/else 없이 붙이기 위한 registry
- `ARCHITECTURE_V5_LANGGRAPH.md`: 구조와 설계 원칙
- `TEST/test_v5_langgraph_architecture.py`: v5 회귀 테스트

## LangGraph 변경

기존:

`START -> cache -> route -> spec -> ambiguity -> worker -> evidence -> verifier -> answer -> finalize`

v5:

`START -> query_analysis -> cache -> route -> spec -> ambiguity -> specialist_worker -> evidence_hub -> evidence_coverage -> verifier -> answer -> claim_grounding -> finalize`

근거 domain이 누락되면 `safe_stop`으로 이동해 LLM이 부족한 근거를 채워 쓰지 못하도록 했습니다.

## 호환성

기존 v4의 router, product DB, Legal DB/guardrail, calculation worker/verifier, cache, conversation resolver, response envelope를 제거하지 않았습니다. v5 계층은 기존 경로의 앞뒤에 추가되어 regression risk를 낮췄습니다.

## 다음 고도화 권장

실제 제공 평가셋으로 baseline을 확정한 뒤 `BM25/FTS + dense retrieval + reranker`를 feature flag로 추가하고 Recall@k/MRR/nDCG 및 answer faithfulness를 A/B 비교하십시오. 임베딩/재랭커는 대회 LLM 제한 및 허용 API를 확인한 뒤 확정하는 것이 안전합니다.
