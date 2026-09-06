"""Regression tests for semantic router / calculation / fact planner (no Gold cloning)."""

from __future__ import annotations

from decimal import Decimal

from chatbot.calculation_gateway import tax_credit_spec, _iter_amounts
from chatbot.calculation_worker import CalculationWorker, PolicyRule
from chatbot.query_router import QueryRouter
from chatbot.required_facts import build_fact_plan
from chatbot.task_intent import classify_task_intent


def test_router_procedure_not_product_catalog():
    router = QueryRouter(product_hints=("미래에셋솔로몬단기국공채", "퇴직연금"))
    q = "잔고가 남은 소규모 펀드도 실물이전이 가능한가요? 안 되면 사유 코드가 있나요?"
    decision = router.decide(q)
    assert "product" not in decision.tools
    assert "document" in decision.tools


def test_router_education_not_product():
    router = QueryRouter(product_hints=("퇴직연금", "DB다같이"))
    q = "가입자 교육을 건너뛰면 과태료가 있나요?"
    decision = router.decide(q)
    assert decision.route.startswith("document") or "document" in decision.tools
    assert "product" not in decision.tools


def test_router_named_product_attribute_still_product():
    router = QueryRouter(product_hints=("흥국멀티크레딧증권자투자신탁",))
    q = "흥국멀티크레딧증권자투자신탁 종류 A 클래스 총보수율은?"
    decision = router.decide(q)
    assert "product" in decision.tools


def test_amount_parse_man_won_spacing():
    amounts = _iter_amounts("총급여 6,000만 원인 직장인이 1,500만 원을 납입했습니다.")
    values = [item[1] for item in amounts]
    assert Decimal("60000000") in values
    assert Decimal("15000000") in values


def test_tax_credit_spec_separates_salary_and_contribution():
    q = "총급여 6,000만 원인 직장인인데요. IRP에 1,500만 원을 납입했습니다. 16.5%로 247만 원 환급이 맞나요?"
    spec = tax_credit_spec(q, policy_year=2026)
    assert not isinstance(spec, dict)
    assert spec.provided_inputs["mode"] == "amount"
    assert Decimal(str(spec.provided_inputs["contribution_amount"])) == Decimal("15000000")
    assert Decimal(str(spec.provided_inputs["gross_salary"])) == Decimal("60000000")
    assert spec.provided_inputs.get("premise_check") == "true"


def test_tax_credit_worker_applies_effective_rate_and_cap():
    rule = PolicyRule(
        formula_id="t",
        version="v",
        source="test",
        tax_credit_rate=Decimal("0.12"),
        lower_income_tax_credit_rate=Decimal("0.15"),
        contribution_limit=Decimal("9000000"),
        pension_savings_limit=Decimal("6000000"),
        annual_contribution_limit=Decimal("18000000"),
        local_tax_surcharge_ratio=Decimal("0.10"),
        gross_salary_threshold=Decimal("55000000"),
        comprehensive_income_threshold=Decimal("45000000"),
    )
    from chatbot.calculation_worker import CalculationQuerySpec

    spec = CalculationQuerySpec(
        calculation_type="tax_credit",
        policy_year=2026,
        required_inputs=["contribution_amount"],
        provided_inputs={
            "contribution_amount": "15000000",
            "gross_salary": "60000000",
            "mode": "amount",
            "premise_check": "true",
            "claimed_rate_percent": "16.5",
        },
    )
    result = CalculationWorker({2026: rule}).run(spec)
    assert not isinstance(result, dict)
    assert Decimal(result.result) == Decimal("1188000")
    assert result.intermediate_values["effective_rate"] == "0.1320"


def test_limit_summary_includes_annual_contribution_when_configured():
    rule = PolicyRule(
        formula_id="t",
        version="v",
        source="test",
        tax_credit_rate=Decimal("0.12"),
        lower_income_tax_credit_rate=Decimal("0.15"),
        contribution_limit=Decimal("9000000"),
        pension_savings_limit=Decimal("6000000"),
        annual_contribution_limit=Decimal("18000000"),
        local_tax_surcharge_ratio=Decimal("0.10"),
    )
    from chatbot.calculation_worker import CalculationQuerySpec

    spec = CalculationQuerySpec(
        calculation_type="tax_credit",
        policy_year=2026,
        required_inputs=[],
        provided_inputs={"mode": "limit_summary"},
    )
    result = CalculationWorker({2026: rule}).run(spec)
    assert not isinstance(result, dict)
    assert result.intermediate_values.get("annual_contribution_limit") == "18000000"


def test_order_request_intent_and_fact_plan():
    q = "대기성자금 전액으로 해당 펀드를 지금 즉시 매수 주문 처리해 주세요."
    assert classify_task_intent(q).primary == "action_request"
    plan = build_fact_plan(q)
    assert plan.stop_reason == "ACTION_NOT_ALLOWED"


def test_order_narrative_not_action_request():
    q = "아침 A유형을 매수하고 주문 처리가 끝난 오후에 B유형을 추가로 매수하는 방식이 가능한가요?"
    assert classify_task_intent(q).primary != "action_request"


def test_tax_limit_route_includes_document():
    router = QueryRouter(product_hints=())
    q = "연금저축과 IRP를 합친 연간 납입 한도와 세액공제 한도는 각각 얼마인가요?"
    decision = router.decide(q)
    assert "calculation" in decision.tools
    assert "document" in decision.tools or decision.route.startswith("document")


def test_isa_transfer_spec_and_worker():
    q = "만기 ISA 3,000만 원을 IRP로 전환 납입했습니다. 올해 추가 세액공제 가능 금액은?"
    spec = tax_credit_spec(q, policy_year=2026)
    assert not isinstance(spec, dict)
    assert spec.provided_inputs["mode"] == "isa_transfer"
    assert Decimal(str(spec.provided_inputs["isa_transfer_amount"])) == Decimal("30000000")
    rule = PolicyRule(
        formula_id="pension_tax_credit_v2026",
        version="v",
        source="test",
        tax_credit_rate=Decimal("0.12"),
        lower_income_tax_credit_rate=Decimal("0.15"),
        contribution_limit=Decimal("9000000"),
        pension_savings_limit=Decimal("6000000"),
        isa_extra_credit_base_limit=Decimal("3000000"),
        isa_transfer_credit_ratio=Decimal("0.10"),
    )
    from chatbot.calculation_worker import CalculationQuerySpec

    result = CalculationWorker({2026: rule}).run(spec)
    assert not isinstance(result, dict)
    assert Decimal(result.result) == Decimal("3000000")


def test_prospectus_limit_is_product_not_institution():
    q = "'미래에셋아세안셀렉트Q연금저축증권전환형자투자신탁1호(주식)' 약관의 소비자 관련주 투자 의무 비율은?"
    assert classify_task_intent(q).primary == "product_attribute"
    decision = QueryRouter(product_hints=("미래에셋아세안셀렉트",)).decide(q)
    assert "product" in decision.tools


def test_risk_asset_question_not_false_correction():
    q = "DC 계좌 적립금으로 일반 주식형 펀드를 100% 전액 매수했습니다. 한도 없이 전액 매수가 가능한가요?"
    assert classify_task_intent(q).primary != "correction"


def test_tax_carry_routes_document_law():
    q = "세액공제 한도를 초과 납입한 금액을 다음 해로 이월해서 공제받는 방법이 있나요?"
    decision = QueryRouter().decide(q)
    assert decision.route in {"document+law", "document", "calculation+law"}
    assert "document" in decision.tools or "calculation" in decision.tools


def test_fee_premise_correction_not_product_catalog():
    q = "모자형 구조 펀드 투자설명서의 자펀드 총보수율만 보면 실제 연간 비용을 완벽히 계산하는 것이 맞나요?"
    assert classify_task_intent(q).primary == "correction"
    decision = QueryRouter().decide(q)
    assert "product" not in decision.tools
    assert "document" in decision.tools or "calculation" in decision.tools


def test_holding_without_name_needs_clarification():
    q = "제가 보유한 TDF 수익률과 다른 국공채 펀드 보수를 비교해 주세요."
    assert classify_task_intent(q).primary == "compound_holding"
    plan = build_fact_plan(q)
    assert plan.stop_reason == "NEEDS_CLARIFICATION"
    assert "holding_product_name" in plan.clarify_fields
