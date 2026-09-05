# v5 LangGraph Multi-Agent Architecture

목표는 대회 과제의 **정확성·근거 완전성·요구사항 충족·환각 억제·정보한계 대응**을 실행 그래프에 직접 반영하는 것입니다. 기존 v4의 Legal DB, Product DB, deterministic calculation, evidence hub, verifier를 보존하고 그 앞뒤에 적응형 분석/검증 계층을 추가했습니다.

## Graph policy

`Conversation -> Query Analysis -> Route/Planner -> Specialist Worker -> Evidence Hub -> Evidence Coverage -> Rule Verifier -> Answer -> Claim Grounding -> Finalize`

- Query Analysis: intent/entity/complexity/missing-information을 결정론적으로 추출합니다.
- Specialist Worker: document / product / law / calculation 경로를 기존 검증된 구현에 위임합니다.
- Evidence Coverage: 실행 계획이 요구한 domain 근거가 모두 존재하는지 검사합니다.
- Rule Verifier: 기존 v4의 Product/Law/Calculation 검증 규칙을 유지합니다.
- Claim Grounding: 최종 답변에 새로 등장한 숫자가 검색 근거나 계산 결과에 존재하는지 검사합니다.
- Safe stop: 근거가 불충분하면 추측하지 않습니다.

## Multi-agent design principle

LLM이 필요한 판단과 deterministic tool을 분리합니다. Supervisor/Answer 같은 판단 노드만 LLM을 사용할 수 있고, 검색·DB·계산·형식 검증은 코드 노드가 수행합니다. 제출환경에서는 LLM provider를 HyperCLOVA X로 고정해야 합니다.

## Extensibility

`chatbot/domain_registry.py`가 도메인 확장 seam입니다. ISA/ETF/보험 등 신규 영역은 core graph를 if/else로 키우기보다 capability + worker adapter로 추가하는 것을 목표로 합니다.

## Research-inspired choices

- RQ-RAG: 복합 질의에서 rewrite/decomposition/disambiguation을 분리하는 방향.
- Modular/adaptive RAG reviews: 모든 질문에 동일한 비용의 RAG를 강제하지 않고 complexity에 따라 planning 강도를 조정.
- GraphRAG literature: 독립 chunk뿐 아니라 entity/relation 구조를 향후 relation layer로 확장 가능하도록 domain registry를 분리.

v5는 의존성을 급격히 늘리지 않는 **backward-compatible competition patch**입니다. Cross-encoder reranker나 dense embedding은 실제 HyperCLOVA X/허용 인프라의 embedding API와 평가셋이 확정된 뒤 feature flag로 넣는 것을 권장합니다.
