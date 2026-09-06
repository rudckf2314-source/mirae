from __future__ import annotations

import time

from chatbot.conversation_resolver import ConversationResolver
from chatbot.pension_ambiguity import AmbiguityGate, SessionContext
from chatbot.product_db_adapter import JsonProductDBAdapter
from chatbot.query_router import QueryRouter


def test_followup_fills_recommendation_slots():
    session = {
        "pending_question": "IRP 상품 추천해줘",
        "missing_fields": ["risk_tolerance", "investment_horizon"],
        "confirmed_constraints": {},
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("나는 위험은 중간 정도, 10년 정도 투자할 거야", session)
    assert out.action == "EXECUTE"
    assert out.context_updates["confirmed_constraints"] == {
        "risk_tolerance": "moderate",
        "investment_horizon": "10년",
    }
    assert out.context_updates["missing_fields"] == []


def test_candidate_followup_is_scoped():
    session = {
        "last_candidates": [
            {"record_id": "r1", "product_name": "A펀드"},
            {"record_id": "r2", "product_name": "B펀드"},
        ],
        "selected_product": {"record_id": "r1", "product_name": "A펀드"},
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("그중에서 보수가 가장 낮은 상품은?", session)
    assert "[후보ID:r1;;r2]" in out.resolved_question or "[후보ID:r1|r2]" in out.resolved_question


def test_singular_product_pronoun_uses_selected_product():
    session = {
        "last_candidates": [{"record_id": "r1", "product_name": "A펀드"}],
        "selected_product": {"record_id": "r1", "product_name": "A펀드"},
        "expires_at": time.time() + 60,
    }
    out = ConversationResolver().resolve("그 상품 위험은?", session)
    assert out.resolved_question.startswith("A펀드")


def test_unbound_product_pronoun_clarifies():
    session = SessionContext(session_id="s", expires_at=time.time() + 60)
    out = AmbiguityGate().decide("이 상품의 1년 수익률을 보여줘", ["product"], session)
    assert out.action == "CLARIFY"
    assert "product_reference" in out.missing_fields


def test_one_year_return_ranking_parser():
    spec = JsonProductDBAdapter._parse_query("최근 1년 수익률이 높은 상품 5개 보여줘", 5)
    assert spec.sort_by == "performance"
    assert spec.sort_order == "desc"
    assert spec.performance_period == "1Y"
    assert spec.performance_metric_type == "fund_return"
    assert spec.limit == 5


def test_total_fee_ranking_routes_to_product():
    decision = QueryRouter().decide("TDF 상품 중 총보수가 낮은 순서대로 보여줘")
    assert decision.tools == ["product"]
    spec = JsonProductDBAdapter._parse_query("TDF 상품 중 총보수가 낮은 순서대로 보여줘", 5)
    assert spec.sort_by == "total_fee"
    assert spec.sort_order == "asc"


def test_hypothetical_example_does_not_require_evidence():
    out = ConversationResolver().resolve("그냥 예시로 안정형 투자자라면 어떻게 추천해?", None)
    assert out.action == "DIRECT"
    assert out.evidence_policy == "NOT_REQUIRED"
    assert "실제 상품 추천이 아니라" in (out.direct_answer or "")
