# Pension Agent Evaluation v0.1 — 즉시 테스트용

이번 버전은 **Legal Guardrail / Rule Mapping 검증을 보류**하고,
현재 챗봇의 Routing / Tool 선택 / Ambiguity / Calculation 분류를 먼저 진단합니다.

## 포함 파일
- `evaluation_dataset_v0.1_test_ready_no_legal_guardrail.json`
- `run_agent_evaluation_v0_1.py`
- `baseline_deterministic_results_v0.1.json`
- `baseline_deterministic_results_v0.1.csv`

## 1. 가장 먼저 실행 — 외부 API/LLM 없이
이 2개 파일을 `pension-chatbot` 프로젝트 루트에 복사한 뒤:

```bash
python run_agent_evaluation_v0_1.py --repo-root . --mode deterministic
```

이 모드는 아래만 import합니다.
- `query_router.py`
- `pension_ambiguity.py`
- `calculation_gateway.py`

따라서 법제처 API, LLM 호출, Product DB 검색 없이 바로 실행할 수 있습니다.

## 2. 실제 Agent 전체 실행
현재 프로젝트의 환경변수/LLM/데이터가 정상이라면:

```bash
python run_agent_evaluation_v0_1.py --repo-root . --mode full --output evaluation_results_full_v0.1.json
```

Full 모드는 `PensionLangGraphAgent`를 실제로 실행합니다.
외부 API나 모델 설정이 부족한 케이스는 전체 테스트를 중단하지 않고 `ERROR`로 기록합니다.

## 채점 범위
현재 자동채점:
- Route
- Required Tools
- Ambiguity action (해당 케이스만)
- Calculation type (해당 케이스만)

현재 수동검토:
- 최종 자연어 답변의 정확성
- 상품 숫자 정확성
- 법령 조문 정확성
- 업무매뉴얼 근거 정확성

Legal Guardrail은 `legal_guardrail_check_enabled=false`로 전부 비활성화되어 있습니다.

## 주의
이 Dataset은 `Gold`가 아니라 **Diagnostic v0.1**입니다.
현재 Agent가 하는 행동을 정답으로 복사한 것이 아니라,
20개 질문의 의도에 맞춘 목표 orchestration을 기준으로 진단합니다.
따라서 FAIL은 테스트 스크립트 오류가 아니라 개선 후보일 수 있습니다.
