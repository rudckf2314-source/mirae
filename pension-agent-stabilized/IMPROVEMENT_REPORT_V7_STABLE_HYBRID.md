# Improvement Report V7 Stable Hybrid

## 작업 요약

V6의 연구형 RAG 기능을 제거하지 않고, 이전 라이브 회귀의 원인이 된 입력 계약/정형 Product schema 해석/상품 identity/세제 multi-source 결합을 최소 수정했다.

## 실제 수정 파일

- chatbot/pension_ambiguity.py
- chatbot/product_db_adapter.py
- chatbot/pension_protocol.py
- chatbot/pension_langgraph_agent.py
- TEST/test_v7_lane_policy_regression.py
- ARCHITECTURE_V7_STABLE_HYBRID.md

## 주요 수정

### 1. 다중턴 input_error 수정
평가기와 UI가 전달하는 `pending_task`를 SessionContext가 허용하도록 계약을 맞췄다. 기존 `extra=forbid` 안전성은 유지한다.

### 2. Product 정형 schema 호환 수정
실제 Standard JSON은 fee_type을 `total_fee` 소문자로 저장하고 performance는 `metric` 필드를 사용한다. 기존 adapter가 uppercase fee type과 `metric_type`만 기대해 T008/T010이 0건이 되던 문제를 수정했다.

### 3. Product identity 우선 resolver
특정 상품/상품군 질의는 일반 lexical ranking보다 먼저 catalog name/family를 좁힌다. 솔로몬 질의는 솔로몬 family만 유지하고 단기/중장기/장기 비교에서는 초단기를 제외한다.

### 4. TDF catalog alias
현재 제공 데이터에서 TDF 역할의 상품명이 `라이프사이클` 계열로 저장되어 있으므로 TDF query를 이 catalog subset으로 deterministic하게 제한한다. LLM이 alias를 생성하지 않는다.

### 5. Tax multi-source
세액공제 계산의 authoritative 숫자는 기존 Legal DB + Rule Engine을 유지하고, 가능한 경우 동일 질문으로 Enterprise docs를 함께 retrieval해 enterprise-first source policy를 만족하도록 했다.

### 6. Response reasoning residue 차단
`We need to`, `The user asks`, `From the evidence` 등 명백한 내부 planning residue를 final response sanitizer에서 제거한다.

## 실제 실행한 테스트

- `python -m compileall -q chatbot tests TEST app.py`: PASS
- `PYTHONPATH=. pytest -q TEST/test_conversation_regression.py TEST/test_agent_patch_regression.py TEST/test_v6_research_rag.py`: 18 passed
- targeted V7 checks: PASS

주의: 이 환경에서는 사용자 로컬 PostgreSQL/HyperCLOVA 라이브 T001~T022를 실행하지 않았다. 따라서 라이브 22/22를 주장하지 않는다.

## 추가 확인 필요

현재 일부 1Y performance 값(예: 98776.0)은 adapter가 이제 정상적으로 읽지만, 값 자체의 스케일/원문 정합성은 upstream extraction 데이터 품질 문제일 수 있다. 라이브 추천 전에 PDF/Standard JSON 원천 검증이 필요하다.
