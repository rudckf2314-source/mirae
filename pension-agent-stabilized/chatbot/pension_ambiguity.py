"""Deterministic Phase 5 ambiguity, default, and retry policy."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .query_router import has_specific_fund_name


POLICY_VERSION = "ambiguity-v2"
Action = Literal["EXECUTE", "CLARIFY", "ASSUME_AND_EXPOSE", "RETRY", "SAFE_STOP"]


class Assumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    value: Any
    reason: str
    impact: Literal["low", "medium", "high"]
    source: str
    policy_version: str = POLICY_VERSION


class AmbiguityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    reason_codes: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    retryable: bool = False
    policy_version: str = POLICY_VERSION


class SessionContext(BaseModel):
    """Optional, caller-owned bounded session context; no persistent memory."""
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    pending_question_id: str | None = None
    confirmed_constraints: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    pending_question: str | None = None
    active_intent: str | None = None
    last_topic: str | None = None
    last_assistant_action: str | None = None
    last_candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_product: dict[str, Any] | None = None
    pending_task: dict[str, Any] | None = None
    expires_at: float

    def active(self, now: float) -> bool:
        return self.expires_at > now


class DefaultPolicyRegistry:
    """Only explicitly registered low-impact defaults can be assumed."""
    defaults = {
        "product_limit": Assumption(
            field="product_limit", value=5, reason="상품 개수가 지정되지 않았습니다.",
            impact="low", source="default_policy_registry",
        ),
    }

    @classmethod
    def get(cls, field: str) -> Assumption | None:
        value = cls.defaults.get(field)
        return value.model_copy(deep=True) if value else None


class AmbiguityGate:
    PERSONAL_MARKERS = ("나에게", "내가", "내 경우", "추천", "적합", "어떤 게", "어느 상품")
    RISK_MARKERS = ("위험등급", "위험 등급", "위험", "등급")
    HORIZON_MARKERS = ("투자기간", "기간", "몇 년", "은퇴")
    LEGAL_PERSONAL = ("가능한지", "내 경우", "제가", "나의", "개인")
    HOLDING_MARKERS = (
        "보유", "운용 중", "운용중", "운용 중인",
        "제 연금 계좌", "계좌에서 발생하는",
    )
    COMPARE_MARKERS = ("대조", "비교", "저렴", "가장 낮", "가장 높")
    ACCOUNT_MARKERS = ("DB", "DC", "IRP", "연금저축")

    def decide(
        self,
        question: str,
        tools: list[str],
        session: SessionContext | None = None,
        *,
        named_product: bool = False,
    ) -> AmbiguityDecision:
        from .task_intent import ORDER_MARKERS, classify_task_intent

        q = question.lower()
        confirmed = session.confirmed_constraints if session else {}
        named = named_product or has_specific_fund_name(question)
        intent = classify_task_intent(question)

        if intent.primary == "action_request" or any(marker in question for marker in ORDER_MARKERS):
            # Continue retrieval so product facts can still be explained; finalize
            # answers with an execution-scope notice under status=success (not safe_stop).
            return AmbiguityDecision(
                action="EXECUTE",
                reason_codes=["ACTION_NOT_ALLOWED"],
                missing_fields=[],
                clarifying_questions=[
                    "실제 매수·주문 체결은 이 상담 채널에서 대행하지 않습니다. "
                    "상품 위험등급·특징 안내는 가능하며, 거래는 MTS/HTS 등에서 직접 진행해 주세요."
                ],
            )

        if intent.primary == "compound_holding" and not named and not confirmed.get("holding_product_name"):
            return AmbiguityDecision(
                action="CLARIFY",
                reason_codes=["NEEDS_CLARIFICATION"],
                missing_fields=["holding_product_name"],
                clarifying_questions=["보유 중이신 상품의 정확한 명칭을 알려주시면 수익률·보수 비교를 이어가겠습니다."],
            )

        is_personal = "product" in tools and any(marker in q for marker in self.PERSONAL_MARKERS)
        explicit_filter = bool(re.search(r"\d+\s*등급|온라인|총보수|\d+\s*(개|가지|종)", q))
        if is_personal and not explicit_filter:
            missing = []
            if not confirmed.get("risk_tolerance"):
                missing.append("risk_tolerance")
            if not confirmed.get("investment_horizon"):
                missing.append("investment_horizon")
            if missing:
                questions = []
                if "risk_tolerance" in missing:
                    questions.append("감수 가능한 위험 수준(예: 원금 변동을 어느 정도 감수할 수 있는지)을 알려주세요.")
                if "investment_horizon" in missing:
                    questions.append("예상 투자기간 또는 연금 수령까지 남은 기간을 알려주세요.")
                return AmbiguityDecision(action="CLARIFY", reason_codes=["personalized_product_recommendation"], missing_fields=missing, clarifying_questions=questions[:3])

        if "law" in tools and any(marker in q for marker in self.LEGAL_PERSONAL):
            return AmbiguityDecision(action="CLARIFY", reason_codes=["personal_legal_applicability"], missing_fields=["personal_legal_facts"], clarifying_questions=["적용 여부 판단에 필요한 사실관계(예: 중도인출 사유와 현재 상황)를 알려주세요."])

        # 지시어만 있고 세션에 선택 상품이 없으면 임의 상품을 고르지 않는다.
        if "product" in tools and any(marker in question for marker in ("이 상품", "그 상품", "해당 상품")):
            selected = (session.selected_product if session else None)
            if not selected:
                return AmbiguityDecision(
                    action="CLARIFY",
                    reason_codes=["missing_product_reference"],
                    missing_fields=["product_reference"],
                    clarifying_questions=["어떤 상품을 말씀하시는지 상품명을 알려주세요."],
                )

        if "product" in tools and not named:
            missing: list[str] = []
            questions: list[str] = []
            holding = any(marker in question for marker in self.HOLDING_MARKERS)
            compare = any(marker in question for marker in self.COMPARE_MARKERS)
            has_account = any(marker.lower() in q for marker in self.ACCOUNT_MARKERS)
            if holding and not confirmed.get("holding_product_name"):
                missing.append("holding_product_name")
                questions.append("현재 운용 중인 상품의 정확한 명칭을 알려주세요.")
            last_topic = (session.last_topic if session else None) or ""
            pending_account = ((session.pending_task or {}).get("account_type") if session else None)
            has_prior_candidates = bool(session and session.last_candidates)
            has_account_context = (
                has_account
                or bool(confirmed.get("account_type"))
                or any(marker.lower() in last_topic.lower() for marker in self.ACCOUNT_MARKERS if last_topic)
                or bool(pending_account)
                or has_prior_candidates
            )
            if compare and not has_account_context:
                missing.append("account_type")
                questions.append("비교할 연금 계좌 유형(DB/DC/IRP)을 알려주세요.")
            if missing:
                return AmbiguityDecision(
                    action="CLARIFY",
                    reason_codes=["missing_product_context"],
                    missing_fields=missing,
                    clarifying_questions=questions[:3],
                )

        if "product" in tools and not re.search(r"\d+\s*(개|가지|종)", q):
            default = DefaultPolicyRegistry.get("product_limit")
            if default:
                return AmbiguityDecision(action="ASSUME_AND_EXPOSE", reason_codes=["default_product_limit"], assumptions=[default])
        return AmbiguityDecision(action="EXECUTE")


def is_transient_error(value: Any) -> bool:
    text = str(value).lower()
    return any(token in text for token in ("timeout", "timed out", "connection reset", "429", "http 5", "temporar"))
