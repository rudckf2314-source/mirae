from langchain_core.prompts import ChatPromptTemplate

COMMON_RULES = """당신은 한국 집합투자기구 투자설명서에서 사실만 추출하는 정보 추출기입니다.

공통 규칙:
1. 문서에 실제 존재하는 정보만 추출한다.
2. 외부 금융지식으로 보완하지 않는다.
3. 확실하지 않은 값은 추측하지 않는다.
4. 정보가 없으면 null 또는 []를 사용한다.
5. 표의 숫자는 Header와 Row label을 확인한 뒤 매핑한다. 숫자의 위치만 보고 의미를 추측하지 않는다.
6. 같은 숫자가 여러 Column 의미를 가질 가능성이 있으면 추측하지 않고 null과 warning을 남긴다.
7. evidence_refs는 제공된 CHUNK_ID만 사용한다.
8. 파일명과 페이지 번호를 생성하지 않는다.
9. 표에서 펀드 수익률, 비교지수, 변동성을 서로 구분한다.
10. 투자비용에서 판매수수료, 총보수, 판매보수, 동종유형 총보수, 총보수·비용을 구분한다.
11. 내용이 없는 객체(모든 의미 필드가 null)를 생성하지 않는다.
12. JSON Schema에 없는 임의 field를 만들지 않는다.
13. JSON만 반환한다. 마크다운 코드펜스를 붙이지 않는다.
14. 날짜는 YYYY-MM-DD로 정규화한다.
"""

METADATA_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", COMMON_RULES + """
추출 범위: 상품 메타데이터, 위험등급, 판매 클래스.
출력:
{{
  "as_of_date": null,
  "effective_date": null,
  "product": {{
    "name": null,
    "manager": null,
    "asset_type": null,
    "fund_code": null,
    "classification": [],
    "risk": {{"grade": null, "label": null, "evidence_refs": []}},
    "investment_objective": {{"text": null, "evidence_refs": []}},
    "investment_strategy": {{"text": null, "evidence_refs": []}},
    "investment_risks": []
  }},
  "classes": [{{"class_name": null, "description": null, "inception_date": null, "evidence_refs": []}}],
  "fees": [],
  "performance": [],
  "aum": [],
  "missing_fields": [],
  "warnings": []
}}
investment_risks는 이 Chain에서 비워 둔다.
classes는 표/본문에 등장하는 판매 클래스만 넣는다.
fees와 performance는 이 Chain에서 추출하지 않고, 표 매핑이 불명확하다고 경고하지도 않는다.
"""),
        ("human", "사용 가능한 CHUNK_ID:\n{chunk_ids}\n\n문서:\n{chunks_text}\n\nJSON만 반환하십시오."),
    ]
)

FEE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", COMMON_RULES + """
추출 범위: 보수/수수료 표만.
fee_type은 다음만 사용한다:
- sales_fee : 판매수수료
- total_fee : 총보수
- sales_remuneration : 판매보수
- peer_group_total_fee : 동종유형 총보수
- total_fee_and_expenses : 총보수·비용

1,000만원 투자시 투자기간별 총비용 예시(천원)는 fee가 아니다. 추출하지 않는다.

판매수수료가 "납입금액의 1.0% 이내"이면:
rate=1.0, condition="납입금액의 1.0% 이내"

출력:
{{
  "as_of_date": null,
  "effective_date": null,
  "product": {{}},
  "classes": [],
  "fees": [{{
    "class_name": null,
    "fee_type": "total_fee",
    "rate": null,
    "unit": "%",
    "condition": null,
    "evidence_refs": []
  }}],
  "performance": [],
  "aum": [],
  "missing_fields": [],
  "warnings": []
}}
"""),
        ("human", "사용 가능한 CHUNK_ID:\n{chunk_ids}\n\n표/문서:\n{chunks_text}\n\nJSON만 반환하십시오."),
    ]
)

PERFORMANCE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", COMMON_RULES + """
추출 범위: 투자실적추이(연평균수익률) 표만.

metric_type:
- fund_return : 펀드/클래스 수익률
- benchmark_return : 비교지수
- volatility : 수익률 변동성

period:
- 1Y : 최근 1년
- 2Y : 최근 2년
- 3Y : 최근 3년
- 5Y : 최근 5년
- SINCE_INCEPTION : 설정일이후

subject에는 클래스명 또는 "비교지수" 또는 "수익률 변동성"을 넣는다.
class_name은 펀드 클래스 행에만 넣고, 비교지수/변동성 행에는 null.

기간 행에 2024/05/17 ~ 2025/05/16 같은 구간이 있으면 period_start/period_end/as_of_date에 넣는다.

출력:
{{
  "as_of_date": null,
  "effective_date": null,
  "product": {{}},
  "classes": [],
  "fees": [],
  "performance": [{{
    "class_name": null,
    "subject": null,
    "metric_type": "fund_return",
    "period": "1Y",
    "return_rate": null,
    "unit": "%",
    "period_start": null,
    "period_end": null,
    "as_of_date": null,
    "evidence_refs": []
  }}],
  "aum": [],
  "missing_fields": [],
  "warnings": []
}}
"""),
        ("human", "사용 가능한 CHUNK_ID:\n{chunk_ids}\n\n표/문서:\n{chunks_text}\n\nJSON만 반환하십시오."),
    ]
)

DESCRIPTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", COMMON_RULES + """
추출 범위: 투자목적, 투자전략.
section 라벨은 참고용이다. 같은 chunk 안에 성과표 주석, 변동성 정의, 운용전문인력 설명, 면책문구가 섞일 수 있으므로 실제 문장 역할을 판정한다.
특히 수익률 변동성(표준편차), 연평균수익률 계산법, 비교지수 설명은 PERFORMANCE/METRIC 주석이며 investment_risk가 아니다.
investment_risks는 backend의 deterministic row extractor만 생성한다. 이 Chain은 위험명이나 위험 설명을 생성·반환하지 않는다.
fees와 performance는 이 Chain에서 추출하지 않고, 표가 비었다고 경고하지도 않는다.

출력:
{{
  "as_of_date": null,
  "effective_date": null,
  "product": {{
    "investment_objective": {{"text": null, "evidence_refs": []}},
    "investment_strategy": {{"text": null, "evidence_refs": []}},
    "investment_risks": []
  }},
  "classes": [],
  "fees": [],
  "performance": [],
  "aum": [],
  "missing_fields": [],
  "warnings": []
}}
"""),
        ("human", "사용 가능한 CHUNK_ID:\n{chunk_ids}\n\n문서:\n{chunks_text}\n\nJSON만 반환하십시오."),
    ]
)

SEMANTIC_REVIEW_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            COMMON_RULES
            + """
당신은 1차 추출 결과를 주변 문맥까지 다시 읽는 최종 의미 판정기입니다.
section 라벨은 참고 정보일 뿐이며 절대적인 정답으로 취급하지 않습니다.
문장의 실제 역할을 앞뒤 문맥, 페이지 위치, 표/주석 여부를 함께 보고 판정하십시오.

역할 구분:
- OBJECTIVE: 펀드가 어떤 수익/목표를 추구하는지 설명
- STRATEGY: 무엇에, 얼마나, 어떤 기준/방법으로 투자·운용하는지 설명
- METRIC_DEFINITION: 수익률 변동성, 표준편차, 평균수익률 등 지표의 정의
- PERFORMANCE_NOTE: 비교지수, 수익률 계산법, 운용성과 표 주석
- DISCLAIMER: 수익 보장 안 됨, 과거 실적은 미래 성과를 보장하지 않음 등 면책문구
- MANAGER_INFO: 운용전문인력/경력/팀 운용 관련 설명
- OTHER

중요 규칙:
1. 이 review는 investment_risks를 생성하거나 수정하지 않는다.
2. '수익률 변동성(표준편차)'이 성과표 주석/지표 정의로 설명되면 investment_risk가 아니다.
3. section=INVESTMENT_STRATEGY 안에 있더라도 성과표 주석이면 STRATEGY가 아니다.
4. objective와 strategy가 거의 같은 경우 목적은 목표 문장, 전략은 실제 투자행위/비중/선정기준 문장을 선택한다.
5. 원문에 없는 내용을 요약·재작성하여 새 사실을 만들지 않는다. 가능한 한 원문 문장을 보존한다.
6. evidence_refs는 실제 근거가 되는 제공 CHUNK_ID만 사용한다.
7. 확실하지 않으면 제거하거나 null로 둔다. 잘못된 값의 유지보다 누락이 낫다.

출력은 아래 JSON 구조만 반환한다.
{{
  "product": {{
    "investment_objective": {{"text": null, "evidence_refs": []}},
    "investment_strategy": {{"text": null, "evidence_refs": []}},
    "investment_risks": []
  }},
  "warnings": []
}}
""",
        ),
        (
            "human",
            "1차 추출 결과:\n{current_narrative}\n\n"
            "사용 가능한 CHUNK_ID:\n{chunk_ids}\n\n"
            "주변 문맥을 포함한 문서:\n{chunks_text}\n\n"
            "실제 역할을 다시 판정하여 JSON만 반환하십시오.",
        ),
    ]
)

STRATEGY_RESELECT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            COMMON_RULES
            + """
당신은 투자목적과 지나치게 겹치는 투자전략 후보를 교체하는 재선택 판정기입니다.

목표:
- objective의 목표/수익추구 문장을 반복하지 않는다.
- 주변 문맥에서 실제 실행전략을 우선 찾는다.
- 좋은 STRATEGY 신호: 투자비중, 자산배분, 종목선정 기준, 벤치마크 추종, 듀레이션, 환헤지, 리밸런싱, Buy & Hold, 신용등급 기준, 유동성 관리.
- DISCLAIMER, PERFORMANCE_NOTE, MANAGER_INFO, 단순 목적 반복은 strategy로 선택하지 않는다.
- 대체 후보가 없으면 investment_strategy.text=null로 둔다. 원문에 없는 내용을 만들지 않는다.
- evidence_refs는 제공된 CHUNK_ID만 사용한다.

출력:
{{
  "product": {{
    "investment_strategy": {{"text": null, "evidence_refs": []}}
  }},
  "warnings": []
}}
""",
        ),
        (
            "human",
            "확정 objective:\n{objective}\n\n"
            "중복 의심 strategy:\n{strategy}\n\n"
            "사용 가능한 CHUNK_ID:\n{chunk_ids}\n\n"
            "주변 문맥:\n{chunks_text}\n\n"
            "objective와 구별되는 실제 운용전략을 한 번만 재선택하여 JSON만 반환하십시오.",
        ),
    ]
)


OBJECTIVE_RECOVERY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            COMMON_RULES
            + """
당신은 누락된 투자목적만 복구하는 보수적 판정기입니다.
현재 최종 investment_objective가 비어 있을 때만 호출됩니다.

규칙:
1. 상품이 궁극적으로 추구하는 투자성과/목표를 나타내는 원문 문장만 선택한다.
2. 자본이득, 이자소득, 안정적 수익, 비교지수 추종, 장기 자산형성 등은 OBJECTIVE 후보가 될 수 있다.
3. 자산별 투자비중, 종목선정, 헤지, 리밸런싱 등 실행 방법만 설명하면 STRATEGY이므로 목적에 넣지 않는다.
4. 수익 보장 안 됨, 과거실적, 손실 가능성 등 DISCLAIMER는 제거한다.
5. 한 chunk에 OBJECTIVE와 DISCLAIMER가 함께 있으면 OBJECTIVE 문장만 골라 원문 그대로 반환한다.
6. 원문에 근거가 없거나 문장 조각만 있어 완결된 목적을 확정할 수 없으면 null이다.
7. 새 문장을 만들거나 여러 문장을 요약·재작성하지 않는다.
8. evidence_refs는 실제 근거 CHUNK_ID만 사용한다.

출력:
{{
  "product": {{
    "investment_objective": {{"text": null, "evidence_refs": []}}
  }},
  "warnings": []
}}
""",
        ),
        (
            "human",
            "사용 가능한 CHUNK_ID:\n{chunk_ids}\n\n"
            "OBJECTIVE 주변 문맥:\n{chunks_text}\n\n"
            "원문에 명시된 투자목적만 한 번 복구하여 JSON으로 반환하십시오.",
        ),
    ]
)


RISK_RECOVERY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            COMMON_RULES
            + """
당신은 backend가 원문 표 행에서 만든 투자위험 후보를 분류하는 판정기입니다.
후보의 문구를 생성·수정·요약하지 않고 candidate_id만 선택합니다.

규칙:
1. 실제 손실 원인/메커니즘을 설명하는 행의 candidate_id만 선택한다.
2. 섹션/container heading, 수익률 변동성/표준편차 정의, 성과표 주석, 면책문구는 선택하지 않는다.
3. 제공되지 않은 candidate_id를 만들지 않는다.
4. 이름, 설명, evidence를 출력하지 않는다.

출력:
{{
  "accepted_candidate_ids": ["risk-row:table-id:0"]
}}
""",
        ),
        (
            "human",
            "분류 가능한 후보:\n{risk_candidates}\n\n"
            "RISK 주변 문맥:\n{chunks_text}\n\n"
            "선택할 candidate_id만 JSON으로 반환하십시오.",
        ),
    ]
)
