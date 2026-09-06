"""Required-fact planning before answering (no invented values)."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from .task_intent import classify_task_intent


@dataclass
class RequiredFact:
    fact_id: str
    description: str
    tool: str
    status: str = "pending"  # pending | matched | missing | not_applicable
    evidence_ref: str | None = None


@dataclass
class FactPlan:
    intent: str
    facts: list[RequiredFact] = field(default_factory=list)
    stop_reason: str | None = None
    clarify_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "facts": [asdict(item) for item in self.facts],
            "stop_reason": self.stop_reason,
            "clarify_fields": list(self.clarify_fields),
            "matched": sum(1 for item in self.facts if item.status == "matched"),
            "missing": sum(1 for item in self.facts if item.status == "missing"),
        }


def build_fact_plan(question: str) -> FactPlan:
    intent = classify_task_intent(question)
    plan = FactPlan(intent=intent.primary)

    if intent.primary == "action_request":
        plan.stop_reason = "ACTION_NOT_ALLOWED"
        plan.facts.append(
            RequiredFact(
                "execution_capability",
                "에이전트가 매수/주문 체결을 수행할 수 있는지",
                "policy",
                status="matched",
                evidence_ref="agent_capability_scope",
            )
        )
        return plan

    if intent.primary in {"procedure", "institution"}:
        plan.facts.extend(
            [
                RequiredFact("applicable_rule", "적용 제도·절차 규칙", "document"),
                RequiredFact("exception_or_code", "예외 사유/코드(해당 시)", "document"),
            ]
        )
        if any(token in question for token in ("법", "규정", "감독")):
            plan.facts.append(RequiredFact("legal_basis", "법령·감독규정 근거", "law"))
        return plan

    if intent.primary in {"tax_calculation", "correction"}:
        plan.facts.extend(
            [
                RequiredFact("policy_year", "적용 정책 연도/기준일", "calculation"),
                RequiredFact("contribution_limit", "연간 납입한도(해당 시)", "calculation"),
                RequiredFact("credit_limit", "세액공제 대상 한도", "calculation"),
                RequiredFact("credit_rate", "적용 공제율(소득 구간)", "calculation"),
            ]
        )
        if any(token in question for token in ("납입했", "환급", "맞나요")):
            plan.facts.append(RequiredFact("user_contribution", "사용자 납입액", "user_input"))
            plan.facts.append(RequiredFact("user_income", "총급여/소득 구간", "user_input"))
        return plan

    if intent.primary in {"product_search", "product_attribute"}:
        plan.facts.extend(
            [
                RequiredFact("product_rows", "조건에 맞는 상품 레코드", "product"),
                RequiredFact("requested_metrics", "요청 지표(보수/수익률/위험 등)", "product"),
            ]
        )
        return plan

    if intent.primary == "compound_holding":
        plan.clarify_fields.append("holding_product_name")
        plan.stop_reason = "NEEDS_CLARIFICATION"
        plan.facts.append(
            RequiredFact(
                "holding_identity",
                "보유 상품 정확한 명칭",
                "user_input",
                status="missing",
            )
        )
        return plan

    plan.facts.append(RequiredFact("primary_evidence", "질문에 직접 답할 기업/법령 근거", "document"))
    return plan


def annotate_plan_with_results(
    plan: FactPlan,
    *,
    has_documents: bool = False,
    has_products: bool = False,
    has_law: bool = False,
    has_calculation: bool = False,
    calculation_inputs: dict[str, Any] | None = None,
) -> FactPlan:
    inputs = calculation_inputs or {}
    for fact in plan.facts:
        if fact.status != "pending":
            continue
        if fact.tool == "document" and has_documents:
            fact.status = "matched"
        elif fact.tool == "product" and has_products:
            fact.status = "matched"
        elif fact.tool == "law" and has_law:
            fact.status = "matched"
        elif fact.tool == "calculation" and has_calculation:
            fact.status = "matched"
        elif fact.tool == "user_input":
            if fact.fact_id == "user_contribution" and inputs.get("contribution_amount"):
                fact.status = "matched"
            elif fact.fact_id == "user_income" and (
                inputs.get("gross_salary") or inputs.get("comprehensive_income")
            ):
                fact.status = "matched"
            else:
                fact.status = "missing"
        elif fact.tool == "policy":
            fact.status = "matched"
    # Partial answers preferred over blanket safe_stop.
    if plan.stop_reason is None and any(f.status == "missing" for f in plan.facts):
        critical = [f for f in plan.facts if f.status == "missing" and f.tool == "user_input"]
        if critical:
            plan.stop_reason = "NEEDS_CLARIFICATION"
            plan.clarify_fields = list(dict.fromkeys(plan.clarify_fields + [f.fact_id for f in critical]))
    return plan
