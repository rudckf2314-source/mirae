"""Display-only Korean labels; internal diagnostics and decisions stay intact."""
import re

FIELDS = {
    "risk_tolerance": "감수할 수 있는 투자 위험 수준",
    "investment_horizon": "예상 투자기간",
    "holding_product_name": "현재 보유한 상품명",
    "account_type": "연금 계좌 종류",
    "personal_legal_facts": "적용 여부를 판단하는 데 필요한 현재 상황",
    "product_reference": "확인하려는 상품명",
    "product_limit": "표시할 상품 수",
    "target_income": "목표 연금액", "expected_income": "예상 연금액",
    "contribution_amount": "납입 금액", "annual_income": "연간 소득",
    "total_salary": "총급여", "income_type": "소득 종류",
    "risk_grade": "위험등급", "risk_grade_max": "최대 위험등급",
    "total_fee": "총보수", "online_only": "온라인 가입 가능 여부",
    "irp_only": "IRP 가입 가능 여부", "pension_type": "연금 유형",
    "conservative": "안정형", "moderate": "중립형", "aggressive": "적극형",
    "true": "예", "false": "아니요", "None": "확인되지 않음",
    "True": "예", "False": "아니요",
    "SAFE_STOP": "현재 자료로는 답변을 확정하기 어려움",
    "NEEDS_CLARIFICATION": "추가 정보가 필요함",
    "ACTION_NOT_ALLOWED": "대신 실행할 수 없는 요청",
    "EVIDENCE_INSUFFICIENT": "판단에 필요한 자료가 부족함",
    "NO_MATCHING_PRODUCT": "조건에 맞는 상품을 찾지 못함",
    "POLICY_BLOCKED": "요청을 처리할 수 없음", "OUT_OF_SCOPE": "지원 범위를 벗어난 질문",
    "ENTERPRISE_DOCUMENT": "참고 자료", "STRUCTURED_DB_EVIDENCE": "상품 자료",
    "PDF_EVIDENCE": "투자설명서 근거", "LAW_EVIDENCE": "법령 근거",
    "DOCUMENT_EVIDENCE": "안내 문서 근거", "FAQ_100": "자주 묻는 질문",
    "PASS": "확인 완료", "FAIL": "확인 필요", "AMBIGUOUS": "추가 확인 필요",
    "CLARIFY": "추가 질문", "EXECUTE": "요청 처리", "NOT_REQUIRED": "자료 조회가 필요하지 않음",
    "ASSUME_AND_EXPOSE": "적용할 조건을 안내한 뒤 답변",
    "UNVERIFIED": "아직 확인되지 않음", "SOURCE_CONFLICT": "자료 내용이 서로 다름",
    "SCALE_MISMATCH": "수치의 단위 확인 필요", "UNIT_MISSING": "단위 확인 필요",
}
DOMAINS = {"product": "상품 자료", "document": "안내 문서", "law": "법령 자료", "calculation": "계산 기준"}
NOTICES = {
    "evidence_status": "참고 자료가 충분하지 않거나 서로 다른 내용이 있어 확인이 필요합니다.",
    "source_versions": "자료의 기준 시점을 확인하지 못했습니다.",
    "document_evidence": "답변을 뒷받침하는 문서의 출처를 확인하지 못했습니다.",
    "law_evidence": "적용할 법령의 원문 근거를 충분히 확인하지 못했습니다.",
    "product_query_parse": "원하시는 상품 조건을 조금 더 구체적으로 알려주세요.",
    "product_count": "요청하신 개수만큼 조건에 맞는 상품을 확인하지 못했습니다.",
    "product_evidence": "해당 상품의 확인 가능한 자료가 부족합니다.",
    "risk_grade": "요청하신 위험등급 조건을 충족하는지 확인이 필요합니다.",
    "online_only": "온라인 가입 가능 여부를 확인하지 못했습니다.",
    "irp_only": "IRP 계좌에서 가입 가능한지 확인하지 못했습니다.",
    "sources_missing": "답변의 출처를 확인하지 못했습니다.",
    "product_source_missing": "상품 정보의 출처를 확인하지 못했습니다.",
    "law_source_missing": "법령 정보의 출처를 확인하지 못했습니다.",
    "calculation_source_missing": "계산에 적용한 근거를 확인하지 못했습니다.",
    "assumption_not_disclosed": "답변에 적용한 조건을 다시 확인해야 합니다.",
    "verification_or_answer_missing": "확인된 근거를 바탕으로 답변을 완성하지 못했습니다.",
    "llm_budget_exceeded": "요청을 한 번에 처리하지 못했습니다. 질문을 나누어 다시 말씀해 주세요.",
    "calculation_required_inputs": "계산에 필요한 금액이나 조건을 추가로 알려주세요.",
    "UNSUPPORTED_POLICY_VERSION": "현재 적용 가능한 계산 기준을 확인하지 못했습니다.",
}
CODE = re.compile(r"(?<![\w])(?:[A-Za-z][A-Za-z0-9]*_)+[A-Za-z0-9]+(?![\w])")


def public_notice(value) -> str:
    text = str(value or "").strip()
    code = text.split(":", 1)[0]
    if code in NOTICES:
        return NOTICES[code]
    if code.startswith(("calculation_", "tax_credit_")):
        return "계산에 사용한 조건이나 결과를 충분히 검증하지 못했습니다."
    if code in FIELDS:
        return FIELDS[code]
    if CODE.search(text) or not re.search(r"[가-힣]", text):
        return "확인이 필요한 항목이 있어 답변에 제한이 있습니다."
    return public_text(text)


def public_text(value) -> str:
    text = "" if value is None else str(value)
    # Keep financial identifiers and original filenames intact.
    protected = []
    def protect(match):
        protected.append(match.group())
        return f"\uFFF0{len(protected)-1}\uFFF1"
    text = re.sub(r"[^\s()\[\]<>]+\.(?:pdf|xlsx?|docx?|csv)\b", protect, text, flags=re.I)
    text = re.sub(r"```(?:python|javascript|js|sql|json)\b[\s\S]*?```",
                  "답변에 필요한 내용을 정리하지 못했습니다. 질문을 조금 더 구체적으로 알려주세요.", text, flags=re.I)
    text = re.sub(r"```(?:text|markdown)?\s*([\s\S]*?)```", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"(?P<num>[\d,]+(?:\.\d+)?)\s*PERCENT_PER_YEAR\b", r"연 \g<num>%", text)
    text = re.sub(r"(?P<num>[\d,]+(?:\.\d+)?)\s*PERCENT_PER_MONTH\b", r"월 \g<num>%", text)
    text = re.sub(r"(?P<num>[\d,]+(?:\.\d+)?)\s*PERCENT\b", r"\g<num>%", text)
    text = re.sub(r"(?P<num>[\d,]+(?:\.\d+)?)\s*KRW\b", r"\g<num>원", text)
    for code, label in sorted(FIELDS.items(), key=lambda item: -len(item[0])):
        text = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(code) + r"(?![A-Za-z0-9_])", lambda _: label, text)
    text = CODE.sub(lambda match: NOTICES.get(match.group(), "추가 확인 항목"), text)
    text = re.sub(r"\uFFF0(\d+)\uFFF1", lambda match: protected[int(match[1])], text)
    return text.strip()


def public_assumption(item: dict) -> dict:
    result = dict(item)
    field = FIELDS.get(str(item.get("field")), "답변에 적용한 조건")
    value = public_text(item.get("value"))
    if item.get("field") == "product_limit":
        label = f"상품 개수를 지정하지 않아 {value}개를 기준으로 안내합니다."
    else:
        label = f"{field}: {value}"
    result["label"] = label
    return result


def source_label(source: dict) -> str:
    name = source.get("source_file")
    if name:
        return str(name)
    return DOMAINS.get(str(source.get("domain")), "참고 자료")
