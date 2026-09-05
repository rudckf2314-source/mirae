from __future__ import annotations

import time

from chatbot.agent_core import PensionAgentCore
from chatbot.conversation_resolver import ConversationResolver
from chatbot.domain_registry import EVIDENCE_POLICY
from chatbot.irp_eligibility import evaluate_irp_eligibility
from chatbot.pension_protocol import ResponseGuard
from chatbot.product_db_adapter import JsonProductDBAdapter, ProductQuerySpec
from tests.agent_eval.evaluators import hard_fails, invented_products


def test_complete_recommendation_slots_resume_pending_task():
    session = {
        "pending_question": "IRP 상품 추천해줘",
        "missing_fields": ["risk_tolerance", "investment_horizon"],
        "confirmed_constraints": {},
        "last_topic": "IRP",
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("나는 위험은 중간 정도, 10년 정도 투자할 거야", session)
    assert out.action == "EXECUTE"
    assert out.context_updates["pending_task"]["intent"] == "PRODUCT_RECOMMENDATION"
    assert out.context_updates["missing_fields"] == []
    assert "위험성향=moderate" in out.resolved_question


def test_last_candidates_persist_in_resolver_scope():
    session = {
        "last_candidates": [
            {"record_id": "r1", "product_name": "A펀드"},
            {"record_id": "r2", "product_name": "B펀드"},
        ],
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("그중에서 보수가 가장 낮은 상품은?", session)
    assert "[후보ID:r1;;r2]" in out.resolved_question or "[후보ID:r1|r2]" in out.resolved_question
    assert out.context_updates["last_candidates"][0]["record_id"] == "r1"


def test_fee_sort_parser_keeps_candidate_ids():
    spec = JsonProductDBAdapter._parse_query("그중에서 보수가 가장 낮은 상품은? [후보ID:r1|r2]", 5)
    assert spec.sort_by == "total_fee"
    assert spec.candidate_record_ids == ("r1", "r2")


def test_selected_product_pronoun_resolution():
    session = {
        "selected_product": {"record_id": "r1", "product_name": "A펀드"},
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("그 상품 위험은?", session)
    assert out.resolved_question.startswith("A펀드")


def test_tdf_alias_and_fee_sort():
    tokens, aliases, _tenors, required = JsonProductDBAdapter._parse_name_constraints(
        "TDF 상품 중 총보수가 낮은 순서대로 보여줘"
    )
    assert required is True
    assert "라이프사이클" in aliases
    spec = JsonProductDBAdapter._parse_query("TDF 상품 중 총보수가 낮은 순서대로 보여줘", 5)
    assert spec.sort_by == "total_fee"
    assert spec.sort_order == "asc"
    assert spec.family_aliases


def test_total_fee_ascending_spec():
    spec = JsonProductDBAdapter._parse_query("TDF 상품 중 총보수가 낮은 순서대로 보여줘", 5)
    assert spec.sort_by == "total_fee"
    assert spec.sort_order == "asc"


def test_family_resolver_three_variants():
    tokens, _aliases, tenors, required = JsonProductDBAdapter._parse_name_constraints(
        "솔로몬 국공채 단기·중장기·장기, 뭐가 달라요?"
    )
    assert required is True
    assert tokens == ("솔로몬", "국공채")
    assert set(tenors) == {"단기", "중장기", "장기"}
    spec = ProductQuerySpec(name_tokens=tokens, tenors=tenors, name_match_required=True)
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "미래에셋솔로몬단기국공채증권자투자신탁1호(채권)"},
        spec,
    )
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "미래에셋솔로몬중장기국공채증권자투자신탁1호(채권)"},
        spec,
    )
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "미래에셋솔로몬장기국공채증권자투자신탁1호(채권)"},
        spec,
    )


def test_unrelated_substitute_prevention():
    spec = ProductQuerySpec(name_tokens=("없는펀드xyz",), name_match_required=True)
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "DB다같이장기채권증권투자신탁[채권]"},
        spec,
    ) is False
    assert JsonProductDBAdapter._matches_name_constraint(
        {"product_name": "DB다같이장기채권증권투자신탁[채권]"},
        ProductQuerySpec(name_tokens=("솔로몬", "국공채"), tenors=("단기", "중장기", "장기"), name_match_required=True),
    ) is False


def test_source_specific_evidence_policy():
    assert EVIDENCE_POLICY["PRODUCT_RECOMMENDATION"]["required"] == ("product",)
    assert "document" in EVIDENCE_POLICY["PRODUCT_RECOMMENDATION"]["optional"]
    assert EVIDENCE_POLICY["TAX_CALCULATION"]["required"] == ("calculation",)
    assert EVIDENCE_POLICY["HYPOTHETICAL_EXAMPLE"]["required"] == ()


def test_final_answer_only_response_guard():
    dirty = (
        "We need to answer the user.\n"
        "From the evidence, structured DB evidence looks fine.\n\n"
        "미래에셋솔로몬단기국공채증권자투자신탁1호(채권)의 위험등급은 5등급입니다."
    )
    cleaned = ResponseGuard._sanitize_answer(dirty)
    assert "We need to" not in cleaned
    assert "structured DB evidence" not in cleaned
    assert "솔로몬" in cleaned


def test_reasoning_leakage_detector():
    reasons = hard_fails(
        expect={"answer_required": True},
        question="IRP 상품 추천해줘",
        envelope={"answer": "We need to pick a fund. 추천 결과는 없습니다.", "status": "success", "metadata": {"route": "product"}},
        trace={"actual_route": "product", "product_count": 1, "product_names": ["A"], "postgres_used": True},
        session_before=None,
        session_after=None,
        catalog={"A"},
        previous_products=[],
    )
    assert any(item.startswith("A:reasoning_leakage") for item in reasons)


def test_safe_stop_with_answer_required_is_fail():
    reasons = hard_fails(
        expect={"answer_required": True, "must_return_product": True, "minimum_products": 1},
        question="나는 위험은 중간 정도, 10년 정도 투자할 거야",
        envelope={"answer": "현재 확인된 기업 제공 자료만으로는", "status": "safe_stop", "metadata": {"route": "product"}},
        trace={"actual_route": "product", "product_count": 0, "product_names": [], "postgres_used": True},
        session_before={"session_id": "s"},
        session_after={"session_id": "s"},
        catalog=set(),
        previous_products=[],
    )
    assert "D:safe_stop_for_required_answer" in reasons
    assert "D:recommendation_candidates_missing" in reasons


def test_compose_product_answer_uses_db_fields_only():
    text = PensionAgentCore.compose_product_answer(
        "IRP 상품 추천해줘",
        [{"product_name": "A펀드", "class_name": "C-P2", "risk_grade": 4, "total_fee": 0.2, "source_file": "a.pdf"}],
    )
    assert "A펀드" in text
    assert "위험등급 4" in text
    assert "0.2" in text
    assert "We need" not in text


def test_c_p2_class_is_irp_investable():
    result = evaluate_irp_eligibility({"class_name": "수수료미징구-오프라인-퇴직연금(C-P2)"})
    assert result["status"] == "ELIGIBLE"


def test_evaluator_does_not_flag_catalog_suffix_as_invented():
    catalog = {"KB 그로스 포커스 증권 자투자신탁(주식)"}
    assert invented_products("KB 그로스 포커스 증권 자투자신탁(주식)의 위험등급", catalog) == []
    assert invented_products("자투자신탁(주식)** 상품", catalog) == []
    assert invented_products("모투자신탁에 90% 이상 투자", catalog) == []
