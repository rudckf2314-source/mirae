from __future__ import annotations

import time

from chatbot.conversation_resolver import ConversationResolver
from chatbot.irp_eligibility import evaluate_irp_eligibility
from chatbot.product_db_adapter import JsonProductDBAdapter, ProductQuerySpec
from tests.agent_eval.evaluators import invented_products


def test_pending_recommendation_resume_reconstructs_task():
    session = {
        "pending_question": "IRP 상품 추천해줘",
        "missing_fields": ["risk_tolerance", "investment_horizon"],
        "confirmed_constraints": {},
        "last_topic": "IRP",
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("나는 위험은 중간 정도, 10년 정도 투자할 거야", session)
    assert out.action == "EXECUTE"
    assert "IRP 상품 추천해줘" in out.resolved_question
    assert out.context_updates["missing_fields"] == []
    assert out.context_updates["pending_task"]["intent"] == "PRODUCT_RECOMMENDATION"
    assert "위험성향=moderate" in out.resolved_question
    assert "위험등급 4등급 이하" not in out.resolved_question


def test_slot_complete_query_is_recommendation_not_risk_lookup():
    spec = JsonProductDBAdapter._parse_query(
        "IRP 상품 추천해줘. 추가 사용자 조건: 위험성향=moderate, 투자기간=10년",
        5,
    )
    assert spec.irp_only is True
    assert spec.risk_grade_max is None
    assert spec.risk_tolerance == "moderate"
    assert "MODERATE" in spec.allowed_risk_buckets
    assert "VERY_AGGRESSIVE" in spec.excluded_risk_buckets
    assert spec.sort_by != "performance"


def test_candidate_scope_keeps_previous_ids():
    session = {
        "last_candidates": [
            {"record_id": "r1", "product_name": "A펀드"},
            {"record_id": "r2", "product_name": "B펀드"},
        ],
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("그중에서 보수가 가장 낮은 상품은?", session)
    assert "[후보ID:r1;;r2]" in out.resolved_question or "[후보ID:r1|r2]" in out.resolved_question


def test_selected_product_pronoun_resolve():
    session = {
        "selected_product": {"record_id": "r1", "product_name": "A펀드"},
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("그 상품 위험은?", session)
    assert out.resolved_question.startswith("A펀드")


def test_performance_metric_reads_schema_metric_field():
    value = JsonProductDBAdapter._performance_value(
        {"performance": [{"metric": "fund_return", "period": "1Y", "value": 3.2}]},
        "fund_return",
        "1Y",
    )
    assert value == 3.2


def test_total_fee_reads_schema_fee_type():
    fee = JsonProductDBAdapter._select_total_fee(
        [{"fee_type": "total_fee_and_expenses", "rate": 0.35, "as_of_date": "2025-01-01"}]
    )
    assert fee is not None
    assert fee["rate"] == 0.35


def test_tdf_alias_is_lifecycle_family():
    tokens, aliases, _tenors, required = JsonProductDBAdapter._parse_name_constraints(
        "TDF 상품 중 총보수가 낮은 순서대로 보여줘"
    )
    assert required is True
    assert "라이프사이클" in aliases


def test_solomon_family_does_not_collapse_to_single_top1():
    tokens, _aliases, tenors, required = JsonProductDBAdapter._parse_name_constraints(
        "솔로몬 국공채 단기·중장기·장기, 뭐가 달라요?"
    )
    assert required is True
    assert tokens == ("솔로몬", "국공채")
    assert set(tenors) == {"단기", "중장기", "장기"}
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "미래에셋솔로몬단기국공채증권자투자신탁1호(채권)"},
        ProductQuerySpec(name_tokens=tokens, tenors=tenors, name_match_required=True),
    )
    assert not JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "DB다같이장기채권증권투자신탁[채권]"},
        ProductQuerySpec(name_tokens=tokens, tenors=tenors, name_match_required=True),
    )


def test_missing_named_product_does_not_match_unrelated_fund():
    spec = ProductQuerySpec(name_tokens=("없는펀드xyz",), name_match_required=True)
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "DB다같이장기채권증권투자신탁[채권]"},
        spec,
    ) is False


def test_evaluator_does_not_flag_catalog_suffix_as_invented():
    catalog = {"KB 그로스 포커스 증권 자투자신탁(주식)"}
    assert invented_products("KB 그로스 포커스 증권 자투자신탁(주식)의 위험등급", catalog) == []
    assert invented_products("자투자신탁(주식)** 상품", catalog) == []


def test_c_p2_class_is_irp_investable():
    result = evaluate_irp_eligibility({"class_name": "수수료미징구-오프라인-퇴직연금(C-P2)"})
    assert result["status"] == "ELIGIBLE"


def test_family_query_keeps_one_row_per_product():
    spec = ProductQuerySpec(name_tokens=("솔로몬", "국공채"), tenors=("단기", "중장기", "장기"), name_match_required=True)
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "미래에셋솔로몬중장기국공채증권자투자신탁1호(채권)"},
        spec,
    )
