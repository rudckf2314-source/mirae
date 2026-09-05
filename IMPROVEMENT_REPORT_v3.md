# Pension Agent 개선 보고서 v3

## 반영 범위
- 회귀평가 결과 T003/T004/T005/T008/T010/T011의 구조적 원인을 production 코드에 최소 변경으로 반영
- 대화 맥락/후보군/선택 상품을 SessionContext에 보존
- 1년 fund_return 정렬 지원
- 총보수 순위 질의를 product route로 강제
- 지시어(이 상품/그 상품) 무맥락 사용 시 임의 상품 선택 금지
- hypothetical recommendation example을 비근거 대화 예시로 처리
- 사용자 노출 응답에서 흔한 CoT 태그/라인 제거
- 이미지에서 제시된 UX를 기능적으로 반영: 새 채팅, 채팅 내역, 설정, 상품추천/포트폴리오/연금도우미 모드, 빠른 질문, 근거 문서 접기

## 주요 변경 파일
- chatbot/conversation_resolver.py
- chatbot/pension_ambiguity.py
- chatbot/query_router.py
- chatbot/product_db_adapter.py
- chatbot/pension_specs.py
- chatbot/agent_core.py
- chatbot/pension_verifier.py
- chatbot/pension_protocol.py
- chatbot/pension_langgraph_agent.py
- app.py
- TEST/test_agent_patch_regression.py

## 검증
- `python -m compileall -q chatbot app.py` PASS
- `PYTHONPATH=. pytest -q TEST/test_conversation_regression.py TEST/test_agent_patch_regression.py` => 13 passed

## 실제 라이브 API E2E
이 작업 환경에서는 사용자의 로컬 PostgreSQL/NVIDIA/HyperCLOVA 환경과 동일한 라이브 서비스가 없으므로 T001~T015 전체 라이브 재실행은 수행하지 않았습니다. 기존 사용자가 제공한 라이브 평가 결과를 원인 분석의 입력으로 사용했고, 코드 수준 회귀 테스트만 실제 실행했습니다.

## 다음 확인 권장
로컬 환경에서 기존 자동평가를 다시 실행:
1. T001~T006 smoke
2. T001~T015 전체
3. repeat=2 재현성
4. 공식 미래에셋 참고질의 5개를 P0 세트로 추가
