from __future__ import annotations

from chatbot.agent_core import PensionAgentCore
from chatbot.display_units import format_financial_value
from chatbot.performance_audit import audit_performance_item
from chatbot.pension_protocol import ResponseGuard
from chatbot.product_db_adapter import JsonProductDBAdapter
from chatbot.risk_policy import (
    bucket_from_record,
    excluded_buckets,
    record_matches_tolerance,
)
from tests.agent_eval.evaluators import hard_fails


def test_fee_unit_formats_percent_per_year():
    assert format_financial_value(0.71, "PERCENT_PER_YEAR", "fee") == "연 0.71%"


def test_raw_unit_enum_not_exposed():
    text = PensionAgentCore.compose_product_answer(
        "IRP 상품 추천해줘",
        [{"product_name": "A펀드", "class_name": "C-P2", "risk_grade": 4, "total_fee": 0.71, "total_fee_unit": "PERCENT_PER_YEAR", "source_file": "a.pdf"}],
    )
    assert "PERCENT_PER_YEAR" not in text
    assert "연 0.71%" in text


def test_performance_raw_display_provenance():
    item = {
        "metric": "fund_return",
        "period": "1Y",
        "value": 3.2,
        "unit": "PERCENT",
        "evidence_ids": ["e1"],
        "source_text": "최근 1년 수익률 3.2%",
    }
    audit = audit_performance_item(item, {"evidence": [{"evidence_id": "e1", "source_text": "최근 1년 수익률 3.2%"}]})
    assert audit["raw_db_value"] == 3.2
    assert audit["status"] == "VERIFIED"


def test_scale_mismatch_detection_fund_code():
    item = {
        "metric": "fund_return",
        "period": "1Y",
        "value": 98776.0,
        "unit": "PERCENT",
        "evidence_ids": ["e1"],
    }
    record = {
        "evidence": [{
            "evidence_id": "e1",
            "source_text": "집합투자기구 명칭 및 펀드코드\n수수료미징구-오프라인-기관(CF)\n98776",
        }]
    }
    audit = audit_performance_item(item, record)
    assert audit["status"] == "SOURCE_CONFLICT"
    assert format_financial_value(98776.0, "PERCENT", "fund_return", status=audit["status"]) == "수익률 값의 단위/스케일 확인이 필요합니다"
    assert format_financial_value(98776.0, "PERCENT", "fund_return", status=audit["status"]) != "9.8776%"


def test_moderate_risk_mapping_uses_semantic_buckets():
    spec = JsonProductDBAdapter._parse_query(
        "IRP 상품 추천해줘. 추가 사용자 조건: 위험성향=moderate, 투자기간=10년",
        5,
    )
    assert spec.risk_grade_max is None
    assert spec.risk_tolerance == "moderate"
    assert spec.allowed_risk_buckets == ("MODERATE", "CONSERVATIVE", "MODERATE_AGGRESSIVE")
    assert "VERY_AGGRESSIVE" in spec.excluded_risk_buckets
    assert record_matches_tolerance({"risk_grade": 4, "risk_label": "보통 위험"}, "moderate")
    assert record_matches_tolerance({"risk_grade": 1, "risk_label": "매우 높은 위험"}, "moderate") is False


def test_excluded_high_risk_candidate_prevention():
    assert bucket_from_record({"risk_grade": 1, "risk_label": "매우 높은 위험"}) == "VERY_AGGRESSIVE"
    assert "VERY_AGGRESSIVE" in excluded_buckets("moderate")
    reasons = hard_fails(
        expect={"must_return_product": True, "risk_tolerance": "moderate"},
        question="IRP 상품 추천해줘",
        envelope={"answer": "후보입니다", "status": "success", "metadata": {"route": "product"}},
        trace={"actual_route": "product", "product_count": 1, "product_names": ["고위험"], "product_risk_grades": [1], "postgres_used": True},
        session_before=None,
        session_after={"confirmed_constraints": {"risk_tolerance": "moderate"}},
        catalog={"고위험"},
        previous_products=[],
    )
    assert any(item.startswith("D:risk_mapping_excluded_bucket") for item in reasons)


def test_product_risk_answer_contains_risk_grade():
    text = PensionAgentCore.compose_product_risk_answer(
        "그 상품 위험은?",
        [{"product_name": "KCGI테스트", "risk_grade": 2, "risk_label": "높은 위험", "investment_risks": [], "source_file": "a.pdf"}],
    )
    assert "투자위험등급은 2등급(높은 위험)" in text
    assert "개방형" not in text
    assert "추가형" not in text
    assert "보수" not in text


def test_product_risk_answer_prefers_investment_risk_narrative():
    text = PensionAgentCore.compose_product_risk_answer(
        "그 상품 위험은?",
        [{
            "product_name": "KCGI테스트",
            "risk_grade": 2,
            "risk_label": "높은 위험",
            "investment_risks": [
                {"subject": "주식가격 변동위험", "text": "주식 가격 하락 위험에 노출됩니다."},
                {"subject": "시장위험", "text": "국내금융시장 변동 위험이 있습니다."},
            ],
            "source_file": "a.pdf",
            "source_pages": [22],
        }],
    )
    assert "주식가격 변동위험" in text
    assert "시장위험" in text
    assert "투자설명서 / 상품 DB 기준" in text


def test_debug_string_leakage_prevention():
    reasons = hard_fails(
        expect={"answer_required": True},
        question="IRP 상품 추천해줘",
        envelope={"answer": "적용한 기본값: product_limit=5\n근거 출처: 상품 PostgreSQL/Standard JSON 구조화 레코드.", "status": "success", "metadata": {"route": "product"}},
        trace={"actual_route": "product", "product_count": 1, "product_names": ["A"], "postgres_used": True},
        session_before=None,
        session_after=None,
        catalog={"A"},
        previous_products=[],
    )
    assert any(item.startswith("A:internal_debug_leak") for item in reasons)


def test_final_answer_only_public_response():
    dirty = (
        "적용한 기본값: product_limit=5 (상품 개수가 지정되지 않았습니다.)\n\n"
        "총보수 0.71PERCENT_PER_YEAR\n근거 출처: 상품 PostgreSQL/Standard JSON 구조화 레코드."
    )
    cleaned = ResponseGuard._sanitize_answer(dirty)
    assert "product_limit=" not in cleaned
    assert "PERCENT_PER_YEAR" not in cleaned
    assert "PostgreSQL/Standard JSON" not in cleaned
    assert "연 0.71%" in cleaned
    assert "투자설명서 / 상품 DB 기준" in cleaned


def test_product_name_investment_grade_not_risk_filter():
    """Product names containing '투자등급' must not trigger risk_grade_not_parseable."""
    from tests.agent_eval.evaluators import source_types_from

    spec = JsonProductDBAdapter._parse_query(
        "미래에셋AI퀀트미국투자등급회사채증권자투자신탁(채권) 위험은?",
        5,
    )
    assert "risk_grade_not_parseable" not in spec.parse_issues

    envelope = {
        "sources": [
            {"domain": "product", "source_file": "a.pdf"},
            {"domain": "document", "source_file": "a.pdf", "source_page": 1},
        ],
        "metadata": {"route": "product"},
    }
    types = source_types_from(envelope, {"product_results": [{}], "final_result": {"pdf_evidence": [{"source_file": "a.pdf"}]}})
    assert "enterprise_document" in types
    assert "product" in types
