# 연금 Agent 프로그램 검토 및 보완 보고서

## 결론
기존 프로그램은 LangGraph, PostgreSQL 상품 DB, 기업 문서 RAG, 법제처 API, 계산 Worker, Evidence Hub/Verifier까지 이미 갖춘 구조로 방향은 적절합니다. 현재 사용자 체감 성능 저하의 주원인은 LLM 자체보다 대화 맥락 전달, follow-up 해석, 사용자에게 노출되는 검증 실패 문구, ResponseEnvelope와 UI 표시 간 불일치였습니다.

## 이번 보완
1. `chatbot/conversation_resolver.py` 추가
   - 직전 추천 질문의 missing slot을 기억하고 `예시/샘플` 요청을 대화성 follow-up으로 처리
   - `중간 정도, 10년` 같은 짧은 답을 이전 추천 질문과 결합
   - `IRP가 뭐야?` 다음 `가입하고 싶은데 어떻게 해?`처럼 생략된 주제를 복원
   - 예시 답변은 실제 금융 사실을 주장하지 않으므로 `NOT_REQUIRED` evidence policy 사용

2. `SessionContext` 확장
   - `pending_question`, `active_intent`, `last_topic`, `last_assistant_action` 추가
   - persistent memory가 아니라 30분 bounded session context로 UI에서 전달

3. Streamlit 대화 상태 연결
   - 이전에는 `agent.respond(question)`만 호출하여 실제로 session context가 전달되지 않았음
   - 이제 `chat_session_context`를 구성해 다음 턴에 전달

4. UI source trace 수정
   - ResponseEnvelope에서는 route가 `metadata.route`에 있는데 UI가 top-level `route`만 읽어 `경로: -`가 표시되던 문제 수정
   - document 질문에는 무조건 `상품 스키마: postgres`가 표시되지 않도록 변경
   - 실제 `sources`의 domain/count를 표시

5. 내부 검증 코드 사용자 노출 제거
   - `source_versions`, `document_evidence`, `FAIL` 같은 내부 verifier 명칭은 audit/metadata에 유지
   - 사용자에게는 근거 부족의 의미만 자연어로 설명

6. 기업 제공 데이터 우선순위 명시
   - docs RAG 검색 결과에 `source_priority=ENTERPRISE_PRIMARY` 추가
   - LLM prompt에 기업 제공 PDF/내부자료가 1차 근거임을 명시
   - 외부 법령이 다를 때 기업 자료를 조용히 덮어쓰지 않고 차이를 밝히도록 수정

7. HyperCLOVA X optional provider 추가
   - `LLM_PROVIDER=hyperclova`일 때 기존 `CLOVA_STUDIO_API_KEY`로 HCX v3 사용 가능
   - 기본값은 기존 NVIDIA 유지하여 기존 동작을 깨지 않음
   - API key는 브라우저/UI에 노출하지 않고 서버 `.env`에서만 읽음

8. Router cache policy version 갱신
   - conversation/router 변경 후 오래된 route cache가 재사용되지 않도록 버전 bump

## API 정책
- 법제처 국가법령정보 API: 기존 구현 유지. 법/제도 최신 사실 확인용 fallback.
- HyperCLOVA X: 기존 Naver Cloud key를 이용하는 선택 옵션 추가.
- OpenDART 등 추가 무료 API는 현재 IRP/퇴직연금 챗봇의 핵심 결함을 해결하지 않아 넣지 않음. API 수를 늘리기보다 기업 제공 158개 자료의 retrieval/routing 품질을 우선.

## 검증
- Python compileall: PASS
- 대화/라우팅/RAG 회귀 테스트: 6/6 PASS
  - 추천 후 예시 요청
  - 짧은 슬롯 후속 입력
  - IRP 가입 follow-up 주제 복원
  - product/document route 분리
  - SessionContext 계약
  - 기업 docs RAG source priority

## 미실행 항목
현재 작업 컨테이너에는 `openai` 패키지가 설치되어 있지 않아 전체 Agent를 실제 NVIDIA/HCX 호출까지 포함한 E2E로 실행하지는 못했습니다. 프로젝트 `requirements.txt`에는 `openai>=1.40.0`이 이미 포함되어 있으므로 사용자 환경에서 설치 후 E2E를 실행해야 합니다.

## 권장 다음 단계
1. `pip install -r requirements.txt`
2. 기존 `.env`는 로컬에만 유지
3. `python -m unittest TEST.test_conversation_regression -v`
4. Streamlit에서 아래 4턴 회귀 테스트
   - IRP 상품 추천해줘
   - 예시 샘플을 줘봐
   - IRP 계좌가 뭐야?
   - 가입하고 싶은데 어떻게 해?
5. 이후 전체 evaluation dataset을 다시 실행해 routing/grounding/UX 지표 비교
