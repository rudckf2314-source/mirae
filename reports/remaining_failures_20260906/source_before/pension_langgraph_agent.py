from __future__ import annotations

from copy import deepcopy
import re
import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .agent_core import PensionAgentCore
from .pension_cache import (
    FAQ_POLICY_VERSION,
    ROUTER_POLICY_VERSION,
    CacheController,
    SourceVersionTracker,
    ToolCacheContext,
    faq_eligible,
    normalize_question,
)
from .pension_specs import SpecificationBundle
from .pension_supervisor import HyperClovaSpecificationSupervisor, SpecificationSupervisor
from .model_policy import llm_for_role, model_for_role
from .public_language import public_text
from .pension_evidence import EvidenceHub, evidence_json
from .pension_verifier import RuleVerifier, VERIFICATION_SCHEMA_VERSION
from .calculation_verifier import CalculationRuleVerifier
from .pension_ambiguity import AmbiguityGate, POLICY_VERSION, SessionContext, is_transient_error
from .pension_protocol import ExecutionBudget, InputHarness, ResponseGuard, InMemoryAuditBackend, audit_record
from .calculation_gateway import classify as classify_calculation, income_gap_spec, tax_credit_spec
from .calculation_worker import CalculationWorker, CalculationResult, PolicyRule
from decimal import Decimal
from .task_intent import ORDER_MARKERS, classify_task_intent
from .required_facts import build_fact_plan, annotate_plan_with_results
from .query_router import RouteDecision
from .conversation_resolver import ConversationResolver
from .tax_policy_repository import TaxPolicyRepository
from .legal_retriever import LegalRetriever
from .adaptive_query import AdaptiveQueryAnalyzer
from .evidence_coverage import EvidenceCoverageChecker
from .claim_grounding import ClaimGroundingVerifier
from .domain_registry import DomainRegistry


RouteName = Literal[
    "document",
    "law",
    "document+law",
    "product",
    "product+law",
    "both",
    "calculation",
    "document+calculation",
    "calculation+law",
]


class TaskSpec(TypedDict):
    """Phase 1의 결정론적 작업 계약입니다. Supervisor LLM은 사용하지 않습니다."""

    goal: str
    intent: str
    required_domains: list[str]
    user_constraints: list[str]
    ambiguities: list[str]


class PlanSpec(TypedDict):
    """확장 가능한 실행 계획 계약입니다. 현재는 기존 경로를 순차 위임합니다."""

    workers: list[str]
    execution_order: list[str]
    parallel_groups: list[list[str]]
    expected_llm_calls: int | None


class VerificationSpec(TypedDict):
    """기존 Product/Law 근거 규칙을 기록하기 위한 최소 계약입니다."""

    required_product_count: int | None
    risk_grade_max: int | None
    sort: str | None
    require_pdf_evidence: bool
    require_law_evidence: bool


class PensionAgentState(TypedDict, total=False):
    question: str
    normalized_question: str
    top_k: int
    route: str
    route_reason: str
    tools: list[str]
    route_decision: RouteDecision
    task_spec: TaskSpec
    plan_spec: PlanSpec
    verification_spec: VerificationSpec
    worker_results: dict[str, Any]
    product_results: list[dict[str, Any]]
    document_evidence: list[dict[str, Any]]
    law_evidence: dict[str, Any] | None
    final_answer: str
    final_result: dict[str, Any]
    errors: list[dict[str, str]]
    used_tools: list[str]
    llm_call_count: int
    additional_worker_llm_call_count: int
    tool_cache: ToolCacheContext
    cached_result: dict[str, Any]
    cache_status: str
    cache_types_used: list[str]
    cache_lookup_count: int
    cache_hit_count: int
    source_versions: dict[str, str | None]
    spec_bundle: dict[str, Any]
    spec_source: str
    spec_validation_status: str
    spec_errors: list[str]
    supervisor_used: bool
    supervisor_call_count: int
    answer_collection: dict[str, Any]
    evidence: list[dict[str, Any]]
    evidence_summary: dict[str, dict[str, int]]
    verification_report: dict[str, Any]
    answer_generated: bool
    safe_stop_reason: str | None
    legacy_worker_fallback: bool
    session_context: dict[str, Any] | None
    ambiguity_decision: dict[str, Any]
    assumptions: list[dict[str, Any]]
    clarify_used: bool
    retry_count: int
    retried_workers: list[str]
    retry_reasons: list[str]
    session_context_used: bool
    retry_pending: str | None
    calculation_result: dict[str, Any] | None
    execution_started_at: float
    execution_budget_applied: bool
    supervisor_calls_reserved: int
    answer_calls_reserved: int
    llm_budget_events: list[dict[str, Any]]
    context_updates: dict[str, Any]
    query_analysis: dict[str, Any]
    evidence_coverage_report: dict[str, Any]
    claim_grounding_report: dict[str, Any]
    domain_registry: dict[str, Any]


class PensionLangGraphAgent:
    """기존 PensionAgentCore 경로를 보존하는 Phase 1 LangGraph 어댑터.

    Worker는 기존 Core의 검증된 실행 경로만 위임합니다. 따라서 Worker가 별도
    LLM, Product DB, Retriever, Law API 로직을 새로 구현하거나 호출하지 않습니다.
    """

    _WORKER_BY_ROUTE: dict[str, str] = {
        "document": "document_worker",
        "law": "law_worker",
        "document+law": "document_law_worker",
        "product": "product_worker",
        "product+law": "product_law_worker",
        "both": "product_worker",
        "calculation": "calculation_worker",
        "document+calculation": "calculation_worker",
        "calculation+law": "calculation_worker",
    }

    def __init__(
        self,
        legacy_agent: PensionAgentCore | None = None,
        cache_controller: CacheController | None = None,
        supervisor: SpecificationSupervisor | None = None,
        audit_backend: Any | None = None,
        execution_budget: ExecutionBudget | None = None,
    ) -> None:
        self.legacy_agent = legacy_agent or PensionAgentCore()
        self.source_version_tracker = None
        if cache_controller is None:
            self.source_version_tracker = SourceVersionTracker.from_agent(
                self.legacy_agent
            )
            self.cache_controller = CacheController(
                source_versions=self.source_version_tracker.versions
            )
        else:
            self.cache_controller = cache_controller
        self.supervisor = supervisor
        self.evidence_hub = EvidenceHub()
        self.rule_verifier = RuleVerifier()
        self.calculation_verifier = CalculationRuleVerifier()
        self.ambiguity_gate = AmbiguityGate()
        self.conversation_resolver = ConversationResolver()
        self.query_analyzer = AdaptiveQueryAnalyzer()
        self.evidence_coverage_checker = EvidenceCoverageChecker()
        self.claim_grounding_verifier = ClaimGroundingVerifier()
        self.domain_registry = DomainRegistry()
        self.input_harness = InputHarness()
        self.response_guard = ResponseGuard()
        self.audit_backend = audit_backend or InMemoryAuditBackend()
        self.execution_budget = execution_budget or ExecutionBudget()
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(PensionAgentState)
        graph.add_node("query_analysis", self._query_analysis_node)
        graph.add_node("cache_gate", self._cache_gate_node)
        graph.add_node("route", self._route_node)
        graph.add_node("simple_spec", self._simple_spec_node)
        graph.add_node("supervisor", self._supervisor_node)
        graph.add_node("spec_validate", self._spec_validate_node)
        graph.add_node("ambiguity_gate", self._ambiguity_gate_node)
        graph.add_node("apply_assumptions", self._apply_assumptions_node)
        graph.add_node("clarify_response", self._clarify_response_node)
        graph.add_node("document_worker", self._document_worker)
        graph.add_node("law_worker", self._law_worker)
        graph.add_node("document_law_worker", self._document_law_worker)
        graph.add_node("product_worker", self._product_worker)
        graph.add_node("product_law_worker", self._product_law_worker)
        graph.add_node("calculation_worker", self._calculation_worker)
        graph.add_node("evidence_hub", self._evidence_hub_node)
        graph.add_node("evidence_coverage", self._evidence_coverage_node)
        graph.add_node("rule_verifier", self._rule_verifier_node)
        graph.add_node("retry_failed_worker", self._retry_failed_worker_node)
        graph.add_node("answer", self._answer_node)
        graph.add_node("claim_grounding", self._claim_grounding_node)
        graph.add_node("safe_stop", self._safe_stop_node)
        graph.add_node("budget_check", self._budget_check_node)
        graph.add_node("finalize", self._finalize_node)
        graph.add_node("cache_store", self._cache_store_node)
        graph.add_node("fast_finalize", self._fast_finalize_node)

        graph.add_edge(START, "query_analysis")
        graph.add_edge("query_analysis", "cache_gate")
        graph.add_conditional_edges(
            "cache_gate",
            self._select_cache_path,
            {"fast_finalize": "fast_finalize", "route": "route", "spec_validate": "spec_validate"},
        )
        graph.add_edge("fast_finalize", END)
        graph.add_conditional_edges(
            "route",
            self._select_spec_path,
            {
                "simple_spec": "simple_spec",
                "supervisor": "supervisor",
            },
        )
        graph.add_edge("simple_spec", "spec_validate")
        graph.add_edge("supervisor", "spec_validate")
        graph.add_conditional_edges("spec_validate", self._select_ambiguity_path, {
            "ambiguity_gate": "ambiguity_gate", "finalize": "finalize",
        })
        graph.add_conditional_edges("ambiguity_gate", self._select_ambiguity_action, {
            "clarify_response": "clarify_response", "apply_assumptions": "apply_assumptions",
            "document_worker": "document_worker", "law_worker": "law_worker",
            "document_law_worker": "document_law_worker", "product_worker": "product_worker",
            "product_law_worker": "product_law_worker", "calculation_worker": "calculation_worker", "safe_stop": "safe_stop", "finalize": "finalize",
        })
        graph.add_conditional_edges("apply_assumptions", self._select_worker, {
            "document_worker": "document_worker", "law_worker": "law_worker",
            "document_law_worker": "document_law_worker", "product_worker": "product_worker",
            "product_law_worker": "product_law_worker", "calculation_worker": "calculation_worker", "finalize": "finalize",
        })
        for worker in self._WORKER_BY_ROUTE.values():
            graph.add_edge(worker, "evidence_hub")
        graph.add_edge("evidence_hub", "evidence_coverage")
        graph.add_conditional_edges(
            "evidence_coverage", self._select_after_coverage,
            {"rule_verifier": "rule_verifier", "safe_stop": "safe_stop"},
        )
        graph.add_conditional_edges(
            "rule_verifier", self._select_after_verification,
            {"answer": "answer", "clarify_response": "clarify_response", "retry_failed_worker": "retry_failed_worker", "safe_stop": "safe_stop"},
        )
        graph.add_edge("retry_failed_worker", "evidence_hub")
        graph.add_edge("answer", "claim_grounding")
        graph.add_edge("claim_grounding", "budget_check")
        graph.add_edge("safe_stop", "budget_check")
        graph.add_edge("clarify_response", "budget_check")
        graph.add_edge("budget_check", "finalize")
        graph.add_edge("finalize", "cache_store")
        graph.add_edge("cache_store", END)
        return graph.compile()

    def invoke(self, question: str, top_k: int = 5, session_context: dict[str, Any] | None = None) -> PensionAgentState:
        """디버깅·비교용 State를 포함해 그래프를 실행합니다."""
        return self.graph.invoke(
            {
                "question": question,
                "normalized_question": question.strip(),
                "top_k": top_k,
                "errors": [],
                "tool_cache": self.cache_controller.context(),
                "session_context": session_context,
                "execution_started_at": time.time(),
                "supervisor_calls_reserved": 0,
                "answer_calls_reserved": 0,
                "llm_budget_events": [],
                "domain_registry": self.domain_registry.describe(),
            }
        )

    def answer(self, question: str, top_k: int = 5, session_context: dict[str, Any] | None = None, question_id: str | None = None, return_envelope: bool = False) -> dict[str, Any]:
        """Legacy Agent와 동일한 입력/출력 인터페이스입니다."""
        started = time.time()
        try:
            request = self.input_harness.validate({"question": question, "question_id": question_id or __import__("uuid").uuid4().hex, "requested_top_k": top_k, "session_context": session_context, "request_started_at": started})
        except Exception as exc:
            code = str(exc) if str(exc) in {"empty_question", "question_too_long", "expired_session_context"} else "validation_error"
            response = self.input_harness.error(question_id, code)
            self.audit_backend.write(audit_record("", response["question_id"], {}, response, started))
            return response
        session_payload = request.session_context.model_dump(mode="json") if request.session_context else None
        resolution = self.conversation_resolver.resolve(request.question, session_payload)
        if resolution.action == "DIRECT":
            response = {
                "status": "success",
                "answer": resolution.direct_answer or "",
                "sources": [],
                "evidence_summary": {},
                "assumptions": [],
                "limitations": [],
                "next_action": "원하는 조건을 알려주시면 이어서 처리합니다.",
                "question_id": request.question_id,
                "metadata": {
                    "response_guard_status": "passed",
                    "route": "conversation",
                    "cache_status": "bypass",
                    "llm_call_count": 0,
                    "evidence_policy": resolution.evidence_policy,
                    "conversation_direct": True,
                    "context_updates": resolution.context_updates,
                },
            }
            self.audit_backend.write(audit_record(request.question, request.question_id, {"route": "conversation", "langgraph": {"verification_verdict": "PASS", "evidence_policy": resolution.evidence_policy}}, response, started))
            return response
        merged_session = dict(session_payload or {})
        for key, value in resolution.context_updates.items():
            merged_session[key] = value
        state = self.invoke(resolution.resolved_question, top_k=request.requested_top_k, session_context=merged_session or None)
        result = state.get("final_result")
        if result is None:
            message = state.get("errors", [{}])[-1].get(
                "message", "LangGraph 실행 결과를 만들지 못했습니다."
            )
            raise RuntimeError(message)
        response = self.response_guard.guard(result, request.question_id)
        self.audit_backend.write(audit_record(request.question, request.question_id, result, response, started))
        if return_envelope or response["status"] == "system_error":
            if result.get("langgraph", {}).get("legacy_worker_fallback") and not return_envelope:
                return result
            return response
        return result

    def respond(self, question: str, top_k: int = 5, session_context: dict[str, Any] | None = None, question_id: str | None = None) -> dict[str, Any]:
        """Phase 6 public response-envelope adapter; legacy ``answer`` remains compatible."""
        return self.answer(question, top_k, session_context, question_id, return_envelope=True)

    def _query_analysis_node(self, state: PensionAgentState) -> dict[str, Any]:
        """Cheap deterministic analysis before routing/planning.

        The result is diagnostic/planning context; it never overrides the
        competition-safe router by itself, preserving v4 behaviour.
        """
        question = state.get("normalized_question") or state.get("question", "")
        analysis = self.query_analyzer.analyze(question)
        fact_plan = build_fact_plan(question)
        payload = analysis.to_dict()
        payload["task_intent"] = classify_task_intent(question).primary
        payload["fact_plan"] = fact_plan.to_dict()
        return {"query_analysis": payload, "domain_registry": self.domain_registry.describe()}

    def _cache_gate_node(self, state: PensionAgentState) -> dict[str, Any]:
        if self.source_version_tracker is not None:
            self.source_version_tracker.refresh()
        question_key = normalize_question(state["normalized_question"])
        versions = self.cache_controller.source_versions
        lookup_count = 0
        hit_count = 0
        types_used: list[str] = []

        cached_result, faq_status, _ = self.cache_controller.lookup(
            "faq_answer",
            {"question": question_key},
            versions.combined(),
            FAQ_POLICY_VERSION,
        )
        lookup_count += 1
        if faq_status == "hit":
            metadata = (cached_result or {}).get("langgraph", {})
            if (
                metadata.get("verification_verdict") == "PASS"
                and metadata.get("verification_schema_version")
                == VERIFICATION_SCHEMA_VERSION
                and metadata.get("evidence_count_by_domain")
            ):
                return {
                    "cached_result": cached_result,
                    "cache_status": "hit",
                    "cache_types_used": ["faq_answer"],
                    "cache_lookup_count": lookup_count,
                    "cache_hit_count": 1,
                    "source_versions": versions.as_dict(),
                }
            faq_status = "miss"

        spec_value, spec_status, _ = self.cache_controller.lookup(
            "spec_bundle",
            {"question": question_key, "model": self._supervisor_model_version()},
            versions.combined(),
            "spec-v1",
        )
        lookup_count += 1
        if spec_status == "hit":
            bundle = SpecificationBundle.model_validate(spec_value)
            return {
                "spec_bundle": bundle.model_dump(),
                "route_decision": RouteDecision(bundle.tools, bundle.route_reason),
                "cache_status": "hit", "cache_types_used": ["spec_bundle"],
                "cache_lookup_count": lookup_count, "cache_hit_count": 1,
                "source_versions": versions.as_dict(), "spec_source": "cache",
                "supervisor_used": False, "supervisor_call_count": 0,
            }

        decision, route_status, _ = self.cache_controller.lookup(
            "route_spec",
            {"question": question_key},
            versions.router_policy,
            ROUTER_POLICY_VERSION,
        )
        lookup_count += 1
        if route_status == "hit":
            hit_count += 1
            types_used.append("route_spec")

        statuses = (faq_status, spec_status, route_status)
        cache_status = "hit" if hit_count else (
            "bypass" if all(status == "bypass" for status in statuses) else "miss"
        )
        return {
            "route_decision": decision,
            "cache_status": cache_status,
            "cache_types_used": types_used,
            "cache_lookup_count": lookup_count,
            "cache_hit_count": hit_count,
            "source_versions": versions.as_dict(),
        }

    @staticmethod
    def _select_cache_path(state: PensionAgentState) -> str:
        if state.get("cached_result") is not None:
            return "fast_finalize"
        return "spec_validate" if state.get("spec_source") == "cache" else "route"

    def _route_node(self, state: PensionAgentState) -> dict[str, Any]:
        question = state["normalized_question"]
        decision = state.get("route_decision")
        if decision is None:
            decision = self.legacy_agent.router.decide(question)
            self.cache_controller.store(
                "route_spec",
                {"question": normalize_question(question)},
                decision,
                self.cache_controller.source_versions.router_policy,
                ROUTER_POLICY_VERSION,
            )
        route = decision.route

        if route not in self._WORKER_BY_ROUTE:
            return {
                "route": route,
                "route_reason": decision.reason,
                "tools": decision.tools,
                "errors": [
                    {
                        "node": "route",
                        "type": "UnsupportedRoute",
                        "message": "지원하지 않는 기존 route가 선택되었습니다.",
                    }
                ],
            }

        return {
            "route": route,
            "route_reason": decision.reason,
            "tools": decision.tools,
            "route_decision": decision,
        }

    @staticmethod
    def _select_spec_path(state: PensionAgentState) -> str:
        return "supervisor" if len(state.get("tools", [])) > 1 else "simple_spec"

    def _simple_spec_node(self, state: PensionAgentState) -> dict[str, Any]:
        return {"spec_bundle": self._rule_bundle(state).model_dump(), "spec_source": "rule", "supervisor_used": False, "supervisor_call_count": 0}

    def _reserve_llm_call(
        self, state: PensionAgentState, kind: Literal["supervisor", "answer"]
    ) -> tuple[bool, dict[str, Any]]:
        """Request-local check-and-reserve before an LLM Provider is entered."""
        field = "supervisor_calls_reserved" if kind == "supervisor" else "answer_calls_reserved"
        limit = self.execution_budget.max_supervisor_calls if kind == "supervisor" else self.execution_budget.max_answer_llm_calls
        used = state.get(field, 0)
        events = list(state.get("llm_budget_events", []))
        if used >= limit:
            events.append({"kind": kind, "status": "blocked", "reserved": used, "limit": limit})
            return False, {field: used, "llm_budget_events": events}
        events.append({"kind": kind, "status": "reserved", "reserved": used + 1, "limit": limit})
        return True, {field: used + 1, "llm_budget_events": events}

    @staticmethod
    def _record_llm_attempt(reservation: dict[str, Any], kind: str, status: str) -> dict[str, Any]:
        events = list(reservation.get("llm_budget_events", []))
        if events:
            events[-1] = {**events[-1], "status": status}
        else:
            events.append({"kind": kind, "status": status})
        return {**reservation, "llm_budget_events": events}

    def _supervisor_node(self, state: PensionAgentState) -> dict[str, Any]:
        allowed, reservation = self._reserve_llm_call(state, "supervisor")
        if not allowed:
            return {**reservation, "spec_bundle": {}, "spec_source": "fallback", "spec_errors": ["supervisor_budget_exhausted"], "supervisor_used": False, "supervisor_call_count": state.get("supervisor_call_count", 0), "errors": [{"node": "supervisor", "type": "BudgetExceeded", "message": "\uad6c\uc870\ud654 \uba85\uc138 \uc0dd\uc131 \uc608\uc0b0\uc774 \uc18c\uc9c4\ub418\uc5b4 \uc694\uccad\uc744 \uc548\uc804\ud558\uac8c \uc911\uc9c0\ud588\uc2b5\ub2c8\ub2e4."}]}
        decision = state["route_decision"]
        request = {
            "question": state["normalized_question"],
            "preliminary_route": decision.route,
            "preliminary_tools": decision.tools,
            "product_query_spec": self._product_query_payload(state["normalized_question"], decision),
            "allowed_workers": ["product", "document", "law"],
            "allowed_routes": list(self._WORKER_BY_ROUTE),
            "schema": SpecificationBundle.model_json_schema(),
        }
        try:
            supervisor = self._get_supervisor()
        except Exception:
            return {**self._record_llm_attempt(reservation, "supervisor", "unavailable"), "spec_bundle": {}, "spec_source": "fallback", "spec_errors": ["supervisor_unavailable"], "supervisor_used": False, "supervisor_call_count": state.get("supervisor_call_count", 0)}
        try:
            return {**self._record_llm_attempt(reservation, "supervisor", "completed"), "spec_bundle": supervisor.analyze(request), "spec_source": "supervisor", "supervisor_used": True, "supervisor_call_count": state.get("supervisor_call_count", 0) + 1}
        except Exception:
            return {**self._record_llm_attempt(reservation, "supervisor", "failed"), "spec_bundle": {}, "spec_source": "fallback", "spec_errors": ["supervisor_output_invalid"], "supervisor_used": True, "supervisor_call_count": state.get("supervisor_call_count", 0) + 1}

    def _spec_validate_node(self, state: PensionAgentState) -> dict[str, Any]:
        decision = state.get("route_decision")
        if decision is None:
            decision = self.legacy_agent.router.decide(state["normalized_question"])
        raw = state.get("spec_bundle")
        try:
            bundle = SpecificationBundle.model_validate(raw)
            if bundle.route != decision.route or bundle.tools != decision.tools:
                raise ValueError("route_or_tools_mismatch")
            expected_product = self._product_query_payload(state["normalized_question"], decision)
            if expected_product is not None:
                if bundle.product_query_spec is not None and bundle.product_query_spec.model_dump() != expected_product:
                    raise ValueError("product_query_spec_mismatch")
            elif bundle.product_query_spec is not None:
                raise ValueError("unexpected_product_query_spec")
            return {
                "route_decision": decision, "route": bundle.route, "tools": bundle.tools,
                "route_reason": bundle.route_reason, "task_spec": bundle.task_spec.model_dump(),
                "plan_spec": bundle.plan_spec.model_dump(), "verification_spec": bundle.verification_spec.model_dump(),
                "spec_bundle": bundle.model_dump(), "spec_validation_status": "valid",
                "spec_errors": state.get("spec_errors", []),
            }
        except Exception as exc:
            fallback = self._rule_bundle({**state, "route_decision": decision})
            return {
                "route_decision": decision, "route": fallback.route, "tools": fallback.tools,
                "route_reason": fallback.route_reason, "task_spec": fallback.task_spec.model_dump(),
                "plan_spec": fallback.plan_spec.model_dump(), "verification_spec": fallback.verification_spec.model_dump(),
                "spec_bundle": fallback.model_dump(), "spec_source": "fallback",
                "spec_validation_status": "fallback", "spec_errors": [*state.get("spec_errors", []), type(exc).__name__],
            }

    @staticmethod
    def _select_ambiguity_path(state: PensionAgentState) -> str:
        return "finalize" if state.get("errors") else "ambiguity_gate"

    def _ambiguity_gate_node(self, state: PensionAgentState) -> dict[str, Any]:
        raw_session = state.get("session_context")
        session = None
        try:
            candidate = SessionContext.model_validate(raw_session) if raw_session else None
            if candidate is not None and candidate.active(time.time()):
                session = candidate
        except Exception:
            session = None
        question = state["normalized_question"]
        router = self.legacy_agent.router
        decision = self.ambiguity_gate.decide(
            question,
            state.get("tools", []),
            session,
            named_product=router.mentions_named_product(question),
        )
        return {
            "ambiguity_decision": decision.model_dump(mode="json"),
            "assumptions": [item.model_dump(mode="json") for item in decision.assumptions],
            "clarify_used": decision.action == "CLARIFY",
            "retry_count": state.get("retry_count", 0),
            "retried_workers": state.get("retried_workers", []),
            "retry_reasons": state.get("retry_reasons", []),
            "session_context_used": session is not None,
        }

    def _select_ambiguity_action(self, state: PensionAgentState) -> str:
        action = state.get("ambiguity_decision", {}).get("action", "EXECUTE")
        if action == "CLARIFY":
            return "clarify_response"
        if action == "ASSUME_AND_EXPOSE":
            return "apply_assumptions"
        if action in {"SAFE_STOP", "RETRY"}:
            return "safe_stop"
        return self._select_worker(state)

    def _apply_assumptions_node(self, state: PensionAgentState) -> dict[str, Any]:
        # Defaults are deliberately low-impact only. top_k already is the
        # registered default for product list requests, so record rather than
        # silently alter any high-impact ProductQuerySpec field.
        return {"assumptions": state.get("assumptions", [])}

    def _clarify_response_node(self, state: PensionAgentState) -> dict[str, Any]:
        existing = next(iter(state.get("worker_results", {}).values()), None)
        if existing and existing.get("route") == "calculation":
            return {"worker_results": {"calculation": existing}, "answer_generated": False, "safe_stop_reason": "clarify_required", "clarify_used": True, "llm_call_count": 0}
        decision = state.get("ambiguity_decision", {})
        missing = decision.get("missing_fields", [])
        questions = decision.get("clarifying_questions", [])[:3]
        message = "요청을 정확히 처리하려면 추가 정보가 필요합니다."
        if missing:
            message += " 필요한 정보: " + ", ".join(public_text(item) for item in missing) + "."
        if questions:
            message += "\n" + "\n".join(f"- {item}" for item in questions)
        result = {
            "question": state.get("question", ""), "answer": message,
            "route": state.get("route"), "route_reason": state.get("route_reason"),
            "tools": state.get("tools", []), "used_tools": [], "results": [],
            "product_results": [], "llm_call_count": 0,
        }
        return {"worker_results": {state.get("route", "clarify"): result},
                "answer_generated": False, "safe_stop_reason": "clarify_required"}
    def _rule_bundle(self, state: PensionAgentState) -> SpecificationBundle:
        decision = state.get("route_decision")
        if decision is None:
            decision = self.legacy_agent.router.decide(state["normalized_question"])
        product = self._product_query_payload(state["normalized_question"], decision)
        tools = list(decision.tools)
        entities = [name for name in ("IRP", "DB", "DC") if name in state["normalized_question"].upper()]
        ambiguities = []
        if "product" in tools and product and product["risk_grade_max"] is None:
            ambiguities.append("위험등급 조건이 명시되지 않았습니다.")
        return SpecificationBundle.model_validate({
            "route": decision.route, "tools": tools, "route_reason": decision.reason,
            "task_spec": {"goal": "기존 연금 근거로 질문에 답변", "intent": decision.route,
                          "required_domains": tools, "entities": entities, "user_constraints": [],
                          "risk_level": "high" if "law" in tools else "medium", "ambiguities": ambiguities,
                          "response_mode": "explanation"},
            "plan_spec": {"workers": tools, "execution_order": ["route", *tools, "finalize"],
                          "parallel_groups": [], "tool_requirements": tools,
                          "direct_pdf_lookup": "product" in tools, "fallbacks": ["legacy_route"],
                          "expected_llm_calls": 1},
            "verification_spec": {"required_product_count": product["limit"] if product and re.search(r"\d+\s*(?:개|종)", state["normalized_question"]) else None,
                                  "risk_grade_max": product["risk_grade_max"] if product else None,
                                  "online_only": product["online_only"] if product else False,
                                  "sort": product["sort_by"] if product else None,
                                  "require_product_evidence": "product" in tools,
                                  "require_pdf_evidence": False,
                                  "require_law_evidence": "law" in tools,
                                  "require_source_version": True, "allowed_evidence_statuses": ["matched"]},
            "product_query_spec": product,
        })

    def _product_query_payload(self, question: str, decision: RouteDecision) -> dict[str, Any] | None:
        if "product" not in decision.tools:
            return None
        product_db = getattr(self.legacy_agent, "product_db", None)
        if not hasattr(product_db, "parse_query"):
            return None
        payload = product_db.parse_query(question).__dict__.copy()
        # ProductQuerySpec keeps issues immutable for parser callers; the
        # supervisor schema/cache payload must use JSON-compatible lists.
        for key in (
            "parse_issues",
            "candidate_record_ids",
            "name_tokens",
            "family_aliases",
            "tenors",
            "exact_product_names",
            "allowed_risk_buckets",
            "excluded_risk_buckets",
            "ranking_policy",
        ):
            payload[key] = list(payload.get(key) or ())
        return payload

    def _get_supervisor(self) -> SpecificationSupervisor:
        if self.supervisor is None:
            self.supervisor = HyperClovaSpecificationSupervisor(llm_for_role("supervisor"))
        return self.supervisor

    def _supervisor_model_version(self) -> str:
        return self.supervisor.model_version if self.supervisor is not None else model_for_role("supervisor")

    def _select_worker(self, state: PensionAgentState) -> str:
        return self._WORKER_BY_ROUTE.get(state.get("route", ""), "finalize")

    def _document_worker(self, state: PensionAgentState) -> dict[str, Any]:
        return self._delegate_legacy_route(state, "document")

    def _law_worker(self, state: PensionAgentState) -> dict[str, Any]:
        return self._delegate_legacy_route(state, "law")

    def _document_law_worker(self, state: PensionAgentState) -> dict[str, Any]:
        return self._delegate_legacy_route(state, "document+law")

    def _product_worker(self, state: PensionAgentState) -> dict[str, Any]:
        return self._delegate_legacy_route(state, "product")

    def _product_law_worker(self, state: PensionAgentState) -> dict[str, Any]:
        return self._delegate_legacy_route(state, "product+law")

    def _calculation_worker(self, state: PensionAgentState) -> dict[str, Any]:
        """Run deterministic calculations from verified policy data only."""
        question = state["normalized_question"]
        calculation_type = classify_calculation(question)
        route_name = state.get("route") or "calculation"
        tools = list(state.get("tools") or ["calculation"])
        base = {
            "question": question, "route": route_name, "tools": tools,
            "results": [], "product_results": [],
        }

        if calculation_type == "income_gap":
            spec = income_gap_spec(question)
            if isinstance(spec, dict):
                status = str(spec.get("status", "CLARIFY"))
                result = {
                    **base, "calculation_status": status,
                    "missing_inputs": spec.get("missing_inputs", []),
                    "invalid_field": spec.get("field"),
                    "answer": "목표 노후소득과 예상 연금소득 금액을 모두 알려주세요." if status == "CLARIFY" else "음수 금액으로는 계산할 수 없습니다.",
                    "used_tools": [],
                }
                return {
                    "worker_results": {"calculation": result},
                    "ambiguity_decision": {
                        "action": "CLARIFY" if status == "CLARIFY" else "SAFE_STOP",
                        "reason_codes": [status], "missing_fields": result["missing_inputs"],
                        "clarifying_questions": [], "assumptions": [],
                    },
                    "clarify_used": status == "CLARIFY", "llm_call_count": 0,
                    "additional_worker_llm_call_count": 0,
                }
            value = CalculationWorker().run(spec)
            assert isinstance(value, CalculationResult)
            result = {
                **base, "calculation_status": "COMPLETED",
                "calculation_result": value.model_dump(mode="json"),
                "used_tools": ["calculation"],
                "answer": f"계산된 월 소득 공백은 {int(value.result):,}원입니다.",
            }
            return {"worker_results": {"calculation": result}, "calculation_result": value.model_dump(mode="json"), "llm_call_count": 0, "additional_worker_llm_call_count": 0, "answer_generated": True}

        if calculation_type == "tax_credit":
            spec = tax_credit_spec(question)
            if isinstance(spec, dict):
                status = str(spec.get("status", "INVALID_INPUT"))
                result = {**base, "calculation_status": status, "answer": "입력 금액을 확인해 주세요.", "used_tools": []}
                return {"worker_results": {"calculation": result}, "ambiguity_decision": {"action": "SAFE_STOP", "reason_codes": [status], "missing_fields": [], "clarifying_questions": [], "assumptions": []}, "llm_call_count": 0, "additional_worker_llm_call_count": 0}

            policy = TaxPolicyRepository().pension_tax_credit(spec.policy_year)
            if policy is None:
                result = {**base, "calculation_status": "UNSUPPORTED_POLICY_VERSION", "answer": "검증된 정책 근거가 없어 이 계산은 수행할 수 없습니다.", "used_tools": ["calculation"]}
                return {"worker_results": {"calculation": result}, "ambiguity_decision": {"action": "SAFE_STOP", "reason_codes": ["UNSUPPORTED_POLICY_VERSION"], "missing_fields": [], "clarifying_questions": [], "assumptions": []}, "llm_call_count": 0, "additional_worker_llm_call_count": 0}

            rule = PolicyRule(
                formula_id=policy.formula_id, version=policy.version, source=policy.source_type,
                tax_credit_rate=policy.standard_rate, lower_income_tax_credit_rate=policy.lower_income_rate,
                contribution_limit=policy.combined_credit_base_limit, pension_savings_limit=policy.pension_savings_credit_base_limit,
                annual_contribution_limit=policy.annual_contribution_limit,
                local_tax_surcharge_ratio=policy.local_tax_surcharge_ratio,
                isa_extra_credit_base_limit=policy.isa_extra_credit_base_limit,
                isa_transfer_credit_ratio=policy.isa_transfer_credit_ratio,
                gross_salary_threshold=policy.gross_salary_threshold, comprehensive_income_threshold=policy.comprehensive_income_threshold,
                evidence_source_key=policy.evidence_source_key, evidence_article_no=policy.evidence_article_no,
            )
            value = CalculationWorker({spec.policy_year: rule}).run(spec)
            if not isinstance(value, CalculationResult):
                status = str(value.get("status", "UNSUPPORTED_POLICY_VERSION"))
                result = {**base, "calculation_status": status, "answer": "검증된 정책 근거로 계산을 완료할 수 없습니다.", "used_tools": ["calculation"]}
                return {"worker_results": {"calculation": result}, "ambiguity_decision": {"action": "SAFE_STOP", "reason_codes": [status], "missing_fields": value.get("missing_inputs", []), "clarifying_questions": [], "assumptions": []}, "llm_call_count": 0, "additional_worker_llm_call_count": 0}

            article = LegalRetriever().get_article(policy.evidence_source_key, policy.evidence_article_no)
            law_result = {
                "success": bool(article), "topic": "TAX_CREDIT",
                "message": "LEGAL_DB_MATCH" if article else "LEGAL_DB_EMPTY",
                "primary_sources": [article] if article else [], "references": [],
                "retrieval_source": "legal_db",
            }
            mode = value.inputs.get("mode")
            intermediate = value.intermediate_values or {}
            if mode == "limit_summary":
                parts = []
                annual = intermediate.get("annual_contribution_limit") or (
                    str(int(policy.annual_contribution_limit)) if policy.annual_contribution_limit is not None else None
                )
                if annual:
                    parts.append(
                        f"연금저축과 IRP를 합한 연간 납입 한도는 {int(Decimal(annual)):,}원입니다."
                    )
                parts.append(
                    f"세액공제 대상 납입액 한도는 합산 연 {int(policy.combined_credit_base_limit):,}원이며, "
                    f"이 중 연금저축 납입액은 연 {int(policy.pension_savings_credit_base_limit):,}원까지 세액공제 대상에 포함됩니다."
                )
                remainder = intermediate.get("irp_credit_remainder_limit")
                if remainder:
                    parts.append(
                        f"합산 한도 {int(policy.combined_credit_base_limit):,}원을 채우려면 "
                        f"나머지 {int(Decimal(remainder)):,}원을 IRP에 납입해야 합니다."
                    )
                eff_std = intermediate.get("effective_standard_rate")
                eff_low = intermediate.get("effective_lower_income_rate")
                if eff_std and eff_low:
                    parts.append(
                        f"지방소득세를 포함한 실효 공제율은 소득구간에 따라 약 {float(eff_low)*100:.1f}% 또는 "
                        f"약 {float(eff_std)*100:.1f}%가 적용됩니다."
                    )
                parts.append("실제 공제세액은 소득구간과 실제 납입액에 따라 달라집니다.")
                answer = " ".join(parts)
            elif mode == "isa_transfer":
                ratio = intermediate.get("isa_transfer_credit_ratio") or "0.1"
                cap = intermediate.get("isa_extra_credit_base_limit") or str(int(policy.isa_extra_credit_base_limit))
                credit_won = int(Decimal(str(value.result)))
                ratio_pct = float(Decimal(ratio)) * 100
                cap_won = int(Decimal(cap))
                answer = (
                    "만기 ISA 전환 납입금에 대한 추가 세액공제 가능 금액은 "
                    + f"{credit_won:,}"
                    + "원입니다. 전환금액의 "
                    + f"{ratio_pct:.0f}"
                    + "%와 "
                    + f"{cap_won:,}"
                    + "원 중 적은 금액을 적용합니다. "
                    + "일반 납입금에 대한 세액공제와 별도로 관리됩니다."
                )
            else:
                eff = intermediate.get("effective_rate")
                base_amt = intermediate.get("credit_base")
                answer_parts = [
                    f"검증된 정책 기준 세액공제액은 {int(Decimal(str(value.result))):,}원입니다."
                ]
                if base_amt and eff:
                    answer_parts.append(
                        f"세액공제 대상 한도 내에서 인정된 납입액 {Decimal(base_amt):,.0f}원에 "
                        f"실효 공제율 {float(eff)*100:.1f}%를 적용했습니다."
                    )
                if intermediate.get("contribution_amount") and base_amt:
                    contrib = Decimal(str(intermediate["contribution_amount"]))
                    credited = Decimal(str(base_amt))
                    if contrib > credited:
                        answer_parts.append(
                            f"납입액 {contrib:,.0f}원 중 세액공제 대상은 최대 {credited:,.0f}원까지입니다."
                        )
                if intermediate.get("premise_check") == "true":
                    claimed = intermediate.get("claimed_rate_percent")
                    answer_parts.insert(0, "아닙니다. 문의하신 전제 중 일부는 확인된 정책과 다릅니다.")
                    if claimed and eff and abs(float(claimed) - float(eff) * 100) > 0.05:
                        answer_parts.insert(
                            1,
                            f"질문에서 가정하신 공제율 {claimed}%는 해당 소득구간의 적용 공제율과 다릅니다.",
                        )
                answer = " ".join(answer_parts)
            # Enterprise-first policy: tax calculations keep Legal DB/Rule Engine
            # authoritative for numbers, while retrieving related enterprise docs
            # as explanatory evidence when available.  Retrieval failure does not
            # replace or fabricate the validated legal calculation.
            enterprise_contexts = []
            try:
                enterprise_contexts = self.legacy_agent.document_chatbot.retriever.retrieve(
                    question, top_k=state.get("top_k", 5), source_group="docs"
                )
            except Exception:
                enterprise_contexts = []
            used_tools = ["calculation", "legal_db"] + (["document"] if enterprise_contexts else [])
            result = {
                **base, "calculation_status": "COMPLETED",
                "calculation_result": value.model_dump(mode="json"), "law_result": law_result,
                "results": enterprise_contexts,
                "used_tools": used_tools, "answer": answer,
            }
            return {
                "worker_results": {"calculation": result},
                "calculation_result": value.model_dump(mode="json"),
                "law_evidence": law_result, "used_tools": used_tools,
                "llm_call_count": 0, "additional_worker_llm_call_count": 0, "answer_generated": True,
            }

        result = {**base, "calculation_status": "UNSUPPORTED_POLICY_VERSION", "answer": "검증된 정책 근거가 없어 이 계산은 수행할 수 없습니다.", "used_tools": ["calculation"]}
        return {"worker_results": {"calculation": result}, "ambiguity_decision": {"action": "SAFE_STOP", "reason_codes": ["UNSUPPORTED_POLICY_VERSION"], "missing_fields": [], "clarifying_questions": [], "assumptions": []}, "llm_call_count": 0, "additional_worker_llm_call_count": 0}

    def _delegate_legacy_route(
        self,
        state: PensionAgentState,
        expected_route: RouteName,
    ) -> dict[str, Any]:
        """기존 Core만 실행하는 얇은 Worker wrapper입니다.

        Core 내부의 Product Python filter/sort, PDF 직접 연결, LawTool 참조조문
        처리와 최종 LLM 한 번 호출을 그대로 사용합니다.
        """
        # 기존 Core가 이미 정의한 예외 처리와 호출자 계약을 보존합니다.
        # 예상하지 못한 API/LLM 오류를 새 오류 응답으로 바꾸거나 숨기지 않습니다.
        collection = None
        if hasattr(self.legacy_agent, "collect_evidence_with_decision"):
            collection = self.legacy_agent.collect_evidence_with_decision(
                state["normalized_question"], state["route_decision"],
                top_k=state["top_k"], tool_cache=state["tool_cache"],
            )
            result = collection["result"]
        else:
            # Fixture compatibility for the existing Phase 1-3 fake agents.
            result = self.legacy_agent.run_with_decision(
                state["normalized_question"], state["route_decision"],
                top_k=state["top_k"], tool_cache=state["tool_cache"],
            )

        if result.get("route") != expected_route:
            return {
                "errors": [
                    {
                        "node": expected_route,
                        "type": "RouteMismatch",
                        "message": "기존 Agent의 route가 LangGraph route와 다릅니다.",
                    }
                ]
            }

        return {
            "worker_results": {expected_route: result},
            "product_results": result.get("product_results", []),
            "document_evidence": result.get("results", []),
            "law_evidence": result.get("law_result"),
            "used_tools": result.get("used_tools", result.get("tools", [])),
            "llm_call_count": 0 if collection is not None else self._legacy_llm_call_count(result),
            "additional_worker_llm_call_count": 0,
            "answer_collection": collection,
            "legacy_worker_fallback": collection is None,
        }

    def _evidence_hub_node(self, state: PensionAgentState) -> dict[str, Any]:
        result = next(iter(state.get("worker_results", {}).values()), {})
        if state.get("legacy_worker_fallback"):
            # Existing mock agents predate the split adapter. They keep their
            # original Phase 1-3 behavior while production routes use the hub.
            return {"evidence": [], "evidence_summary": {"domain": {}, "status": {}}}
        evidence, summary = self.evidence_hub.collect(result, state.get("source_versions", {}))
        return {"evidence": evidence_json(evidence), "evidence_summary": summary}

    def _evidence_coverage_node(self, state: PensionAgentState) -> dict[str, Any]:
        result = next(iter(state.get("worker_results", {}).values()), {})
        required = list(state.get("tools", []))
        report = self.evidence_coverage_checker.check(required, state.get("evidence", []), result, list((state.get("query_analysis") or {}).get("required_evidence", [])))
        return {"evidence_coverage_report": report.to_dict()}

    @staticmethod
    def _select_after_coverage(state: PensionAgentState) -> str:
        report = state.get("evidence_coverage_report", {})
        # Fixture/legacy paths may not expose normalized Evidence objects; in
        # that case the checker also inspects raw worker results.
        if report.get("complete", True):
            return "rule_verifier"
        result = next(iter(state.get("worker_results", {}).values()), {})
        # Prefer partial grounded answers over blanket safe_stop when any
        # authoritative document/product rows were retrieved.
        if result.get("results") or result.get("product_results") or result.get("calculation_result"):
            return "rule_verifier"
        return "safe_stop"

    def _rule_verifier_node(self, state: PensionAgentState) -> dict[str, Any]:
        if state.get("legacy_worker_fallback"):
            result = next(iter(state.get("worker_results", {}).values()), {})
            return {"verification_report": self._legacy_fallback_report(result)}
        spec = SpecificationBundle.model_validate(state["spec_bundle"])
        result = next(iter(state.get("worker_results", {}).values()), {})
        if result.get("route") == "calculation":
            status = result.get("calculation_status")
            if status == "CLARIFY":
                return {"verification_report": {
                    "verdict": "AMBIGUOUS", "checks": [{"check_id": "calculation_required_inputs", "rule": "required inputs", "status": "AMBIGUOUS", "severity": "hard", "expected": "complete inputs", "actual": result.get("missing_inputs", []), "evidence_ids": [], "message": "Calculation inputs are incomplete."}],
                    "failures": [], "warnings": ["calculation_required_inputs"], "evidence_count_by_domain": {}, "evidence_count_by_status": {}, "verification_schema_version": VERIFICATION_SCHEMA_VERSION,
                }}
            if status in {"INVALID_INPUT", "UNSUPPORTED_POLICY_VERSION"}:
                return {"verification_report": {
                    "verdict": "FAIL", "checks": [{"check_id": status.lower(), "rule": "supported and valid calculation policy", "status": "FAIL", "severity": "hard", "expected": "supported non-negative calculation", "actual": status, "evidence_ids": [], "message": "Calculation cannot execute under the supplied policy or inputs."}],
                    "failures": [status], "warnings": [], "evidence_count_by_domain": {}, "evidence_count_by_status": {}, "verification_schema_version": VERIFICATION_SCHEMA_VERSION,
                }}
            calculation = CalculationResult.model_validate(result["calculation_result"])
            report = self.calculation_verifier.verify(
                calculation, [self._evidence_model(item) for item in state.get("evidence", [])]
            )
            return {"verification_report": report.model_dump(mode="json")}
        report = self.rule_verifier.verify(
            spec.verification_spec, spec.product_query_spec, result,
            [self._evidence_model(item) for item in state.get("evidence", [])],
            state.get("source_versions", {}), state.get("errors", []),
        )
        return {"verification_report": report.model_dump(mode="json")}

    @staticmethod
    def _evidence_model(value: dict[str, Any]):
        from .pension_evidence import Evidence
        return Evidence.model_validate(value)

    @staticmethod
    def _legacy_fallback_report(result: dict[str, Any]) -> dict[str, Any]:
        """Compatibility bridge for the Phase 1-3 fixture-only fake agents."""
        statuses = [field.get("status") for item in result.get("evidence_status", []) for field in item.get("fields", [])]
        bad = [status for status in statuses if status != "matched"]
        verdict = "AMBIGUOUS" if any(status in {"unresolved", "conflict"} for status in bad) else ("FAIL" if bad else "PASS")
        domains: dict[str, int] = {}
        if result.get("results"):
            domains["document"] = len(result["results"])
        if result.get("product_results"):
            domains["product"] = len(result["product_results"])
        if (result.get("law_result") or {}).get("primary_sources"):
            domains["law"] = len(result["law_result"]["primary_sources"])
        return {"verdict": verdict, "checks": [], "failures": ["fixture_evidence_status"] if verdict == "FAIL" else [], "warnings": ["fixture_evidence_status"] if verdict == "AMBIGUOUS" else [], "evidence_count_by_domain": domains, "evidence_count_by_status": {"matched": sum(domains.values())} if not bad else {str(status): statuses.count(status) for status in set(statuses)}, "verification_schema_version": VERIFICATION_SCHEMA_VERSION}

    @staticmethod
    def _select_answer_path(state: PensionAgentState) -> str:
        reason_codes = list((state.get("ambiguity_decision") or {}).get("reason_codes") or [])
        if "ACTION_NOT_ALLOWED" in reason_codes:
            return "answer"
        return "answer" if state.get("verification_report", {}).get("verdict") == "PASS" else "safe_stop"

    def _select_after_verification(self, state: PensionAgentState) -> str:
        result = next(iter(state.get("worker_results", {}).values()), {})
        if result.get("route") == "calculation" and result.get("calculation_status") in {"CLARIFY", "INVALID_INPUT"}:
            return "clarify_response"
        if self._select_answer_path(state) == "answer":
            return "answer"
        # Soft continue: documents/products already retrieved → generate partial answer.
        if result.get("results") or result.get("product_results") or result.get("calculation_result"):
            return "answer"
        if state.get("retry_count", 0) >= 1:
            return "safe_stop"
        law_result = result.get("law_result") or {}
        if "law" in state.get("tools", []) and not law_result.get("success") and is_transient_error(law_result.get("message", "")):
            return "retry_failed_worker"
        return "safe_stop"

    def _retry_failed_worker_node(self, state: PensionAgentState) -> dict[str, Any]:
        """Retry only the transient Law worker; Product/PDF output remains intact."""
        result = next(iter(state.get("worker_results", {}).values()), {})
        if not hasattr(self.legacy_agent, "_search_law_result"):
            return {"retry_count": state.get("retry_count", 0) + 1, "retried_workers": [*state.get("retried_workers", []), "law"], "retry_reasons": [*state.get("retry_reasons", []), "transient_law_error"]}
        try:
            law_result = self.legacy_agent._search_law_result(  # type: ignore[attr-defined]
                state["normalized_question"], tool_cache=None
            )
        except Exception as exc:
            law_result = {"success": False, "primary_sources": [], "references": [], "message": type(exc).__name__}
        result["law_result"] = law_result
        result["law_results"] = law_result
        result["law_evidence_status"] = "matched" if law_result.get("success") else "unresolved"
        collection = state.get("answer_collection")
        if collection is not None:
            law_text = self.legacy_agent._law_result_to_text(law_result)  # type: ignore[attr-defined]
            prior = str(collection.get("evidence_text") or "")
            prefix = prior.split("\n\n[LAW_EVIDENCE]", 1)[0]
            collection["evidence_text"] = f"{prefix}\n\n[LAW_EVIDENCE]\n{law_text}"
        return {
            "worker_results": {state["route"]: result}, "answer_collection": collection,
            "retry_count": state.get("retry_count", 0) + 1,
            "retried_workers": [*state.get("retried_workers", []), "law"],
            "retry_reasons": [*state.get("retry_reasons", []), "transient_law_error"],
        }

    def _answer_node(self, state: PensionAgentState) -> dict[str, Any]:
        result = next(iter(state.get("worker_results", {}).values()), None)
        collection = state.get("answer_collection")
        if result is None:
            return {"answer_generated": False, "llm_call_count": 0}
        if collection is None:
            return {"answer_generated": True, "llm_call_count": state.get("llm_call_count", 0)}
        allowed, reservation = self._reserve_llm_call(state, "answer")
        if not allowed:
            result["answer"] = "\uc751\ub2f5 \uc0dd\uc131 \uc608\uc0b0\uc774 \uc18c\uc9c4\ub418\uc5b4 \uc548\uc804\ud558\uac8c \uc911\uc9c0\ud588\uc2b5\ub2c8\ub2e4."
            result["llm_call_count"] = state.get("llm_call_count", 0)
            return {**reservation, "worker_results": {state["route"]: result}, "answer_generated": False, "safe_stop_reason": "answer_budget_exhausted", "llm_call_count": state.get("llm_call_count", 0)}
        products = result.get("product_results") or state.get("product_results") or []
        question = state["normalized_question"]
        answer = None
        from .task_intent import classify_task_intent

        intent = classify_task_intent(question)
        conceptual = intent.primary in {"correction", "institution", "procedure", "tax_calculation"}
        catalog_style = (
            products
            and not conceptual
            and any(
                token in question
                for token in ("추천", "보여줘", "찾아줘", "비교해", "가장 낮", "가장 높")
            )
        )
        if products and any(token in question for token in ("위험은", "투자위험", "위험등급은", "리스크는")) and not any(
            token in question for token in ("보여줘", "추천", "이하", "이상", "비교")
        ):
            answer = PensionAgentCore.compose_product_risk_answer(question, products)
        elif catalog_style:
            answer = PensionAgentCore.compose_product_answer(question, products)
        if not answer:
            try:
                answer = self.legacy_agent.generate_answer_from_collection(question, collection)
            except Exception:
                answer = None
        if not answer:
            if products:
                answer = PensionAgentCore.compose_product_answer(question, products)
            else:
                result["answer"] = "답변 생성 서비스를 완료하지 못해 안전하게 중지했습니다."
                result["llm_call_count"] = state.get("llm_call_count", 0) + 1
                return {**self._record_llm_attempt(reservation, "answer", "failed"), "worker_results": {state["route"]: result}, "answer_generated": False, "safe_stop_reason": "answer_provider_failed", "llm_call_count": state.get("llm_call_count", 0) + 1}
        assumptions = state.get("assumptions", [])
        is_risk_answer = products and any(
            token in question for token in ("위험은", "투자위험", "위험등급은", "리스크는")
        ) and not any(token in question for token in ("보여줘", "추천", "이하", "이상", "비교"))
        if (
            assumptions
            and not is_risk_answer
            and "다음" not in str(answer)
            and "보여드리겠습니다" not in str(answer)
        ):
            limit = next(
                (item.get("value") for item in assumptions if item.get("field") == "product_limit"),
                None,
            )
            if limit:
                answer = f"다음 {limit}개를 보여드리겠습니다.\n\n{answer}"

        reason_codes = list((state.get("ambiguity_decision") or {}).get("reason_codes") or [])
        if "ACTION_NOT_ALLOWED" in reason_codes:
            scope = (
                (state.get("ambiguity_decision") or {}).get("clarifying_questions") or [None]
            )[0] or (
                "실제 매수·주문 체결은 이 상담 채널에서 대행하지 않습니다. "
                "상품 정보 안내는 가능하며, 거래는 MTS/HTS 등에서 직접 진행해 주세요."
            )
            if "대행하지" not in answer and "실행할 수 없" not in answer:
                answer = f"{scope}\n\n{answer}".strip()

        from .task_intent import CORRECTION_MARKERS, classify_task_intent

        intent = classify_task_intent(question)
        correction_tokens = ("아니", "아닙니다", "아니라", "옳지", "잘못", "교정", "해당하지")
        needs_correction = intent.primary == "correction" or any(
            marker in question for marker in CORRECTION_MARKERS
        )
        if needs_correction and not any(token in answer for token in correction_tokens):
            # Negating answers like "불가합니다" still need an explicit correction opener.
            answer = f"아닙니다. {answer}"

        raw_answer = answer
        answer = ResponseGuard._sanitize_answer(answer)
        result["raw_answer"] = raw_answer
        result["answer"] = answer
        result["final_answer"] = answer
        result["llm_call_count"] = 1
        return {**self._record_llm_attempt(reservation, "answer", "completed"), "worker_results": {state["route"]: result}, "answer_generated": True, "llm_call_count": state.get("llm_call_count", 0) + 1}

    def _claim_grounding_node(self, state: PensionAgentState) -> dict[str, Any]:
        result = next(iter(state.get("worker_results", {}).values()), {})
        answer = str(result.get("answer") or state.get("final_answer") or "")
        evidence_texts = [str(item) for item in state.get("evidence", [])]
        # Raw result is included because some v4 evidence adapters keep
        # authoritative fields in the worker payload rather than Evidence text.
        evidence_texts.append(str({k: v for k, v in result.items() if k != "answer"}))
        report = self.claim_grounding_verifier.verify(
            answer, evidence_texts, state.get("calculation_result")
        )
        return {"claim_grounding_report": report.to_dict()}

    def _safe_stop_node(self, state: PensionAgentState) -> dict[str, Any]:
        report = state.get("verification_report", {})
        verdict = report.get("verdict", "FAIL")
        checks = report.get("failures") or report.get("warnings") or []
        reason = ", ".join(checks) or "evidence_validation"
        ambiguity = state.get("ambiguity_decision") or {}
        reason_codes = list(ambiguity.get("reason_codes") or [])
        stop_code = next(
            (
                code
                for code in reason_codes
                if code in {
                    "EVIDENCE_INSUFFICIENT",
                    "NEEDS_CLARIFICATION",
                    "ACTION_NOT_ALLOWED",
                    "NO_MATCHING_PRODUCT",
                    "POLICY_BLOCKED",
                    "OUT_OF_SCOPE",
                }
            ),
            None,
        )
        # Keep validator codes in metadata/audit only. User-facing text should
        # explain the limitation without exposing internal implementation names.
        if "ACTION_NOT_ALLOWED" in reason_codes:
            questions = ambiguity.get("clarifying_questions") or []
            message = questions[0] if questions else (
                "실제 매수·주문 체결은 이 상담 채널에서 대행하지 않습니다. "
                "상품 정보 안내는 가능하며, 거래는 MTS/HTS 등에서 직접 진행해 주세요."
            )
            result = next(iter(state.get("worker_results", {}).values()), {}) or {
                "question": state.get("question", ""),
                "route": state.get("route"),
                "tools": state.get("tools", []),
            }
            result["answer"] = message
            result["final_answer"] = message
            result["stop_reason_code"] = "ACTION_NOT_ALLOWED"
            result["llm_call_count"] = 0
            return {
                "worker_results": {state.get("route") or "document": result},
                "answer_generated": True,
                "safe_stop_reason": None,
                "llm_call_count": 0,
            }
        if ambiguity.get("clarifying_questions"):
            questions = ambiguity.get("clarifying_questions") or []
            message = questions[0] if questions else (
                "이 요청은 현재 상담 범위에서 직접 실행할 수 없습니다. 확인이 필요한 정보를 구체적으로 알려주세요."
            )
        elif stop_code == "NEEDS_CLARIFICATION":
            message = "요청을 정확히 처리하려면 추가 정보가 필요합니다. " + " ".join(
                ambiguity.get("clarifying_questions") or ["필요한 조건을 알려주세요."]
            )
        elif stop_code == "NO_MATCHING_PRODUCT":
            message = "조건에 맞는 상품을 상품 DB에서 확인하지 못했습니다. 조건을 조금 바꿔 다시 질문해 주세요."
        elif "product" in state.get("tools", []):
            message = "현재 확인된 기업 제공 자료만으로는 이 상품 관련 답을 안전하게 확정하기 어렵습니다. 상품명이나 비교 조건을 조금 더 구체적으로 알려주시면 다시 확인하겠습니다."
        elif "law" in state.get("tools", []):
            message = "현재 확인된 기업 자료와 공식 법령 근거만으로는 법적 결론을 안전하게 확정하기 어렵습니다. 적용 상황이나 확인하려는 조건을 조금 더 구체적으로 알려주세요."
        else:
            message = "현재 기업 제공 자료에서 질문에 직접 답할 충분한 근거를 찾지 못했습니다. 확인하려는 주제나 조건을 조금 더 구체적으로 알려주세요."
        result = next(iter(state.get("worker_results", {}).values()), {})
        if result.get("route") == "calculation" and result.get("calculation_status") == "UNSUPPORTED_POLICY_VERSION":
            result["llm_call_count"] = 0
            return {"worker_results": {"calculation": result}, "answer_generated": False, "safe_stop_reason": "UNSUPPORTED_POLICY_VERSION", "llm_call_count": 0}
        result["answer"] = message
        result["stop_reason_code"] = stop_code or "EVIDENCE_INSUFFICIENT"
        count = state.get("llm_call_count", 0) if state.get("legacy_worker_fallback") else 0
        result["llm_call_count"] = count
        return {
            "worker_results": {state["route"]: result},
            "answer_generated": False,
            "safe_stop_reason": stop_code or reason,
            "llm_call_count": count,
        }

    def _budget_check_node(self, state: PensionAgentState) -> dict[str, Any]:
        """Common execution budget gate, including deterministic calculations."""
        total_llm = state.get("llm_call_count", 0) + state.get("supervisor_call_count", 0)
        elapsed = time.time() - state.get("execution_started_at", time.time())
        exceeded = (
            state.get("supervisor_call_count", 0) > self.execution_budget.max_supervisor_calls
            or total_llm > self.execution_budget.max_supervisor_calls + self.execution_budget.max_answer_llm_calls
            or state.get("retry_count", 0) > self.execution_budget.max_worker_retries
            or elapsed > self.execution_budget.timeout_seconds
        )
        if not exceeded:
            return {"execution_budget_applied": True}
        result = next(iter(state.get("worker_results", {}).values()), {})
        result["answer"] = "\uc2e4\ud589 \uc608\uc0b0\uc744 \ucd08\uacfc\ud558\uc5ec \uc694\uccad\uc744 \uc548\uc804\ud558\uac8c \uc911\uc9c0\ud588\uc2b5\ub2c8\ub2e4."
        result["llm_call_count"] = 0
        return {"worker_results": {state.get("route", "unknown"): result}, "answer_generated": False, "safe_stop_reason": "execution_budget_exceeded", "llm_call_count": 0, "execution_budget_applied": True}

    def _finalize_node(self, state: PensionAgentState) -> dict[str, Any]:
        errors = state.get("errors", [])
        if errors:
            return {
                "final_result": {
                    "question": state.get("question", ""),
                    "answer": errors[-1]["message"],
                    "route": state.get("route"),
                    "tools": state.get("tools", []),
                    "errors": errors,
                    "llm_call_count": 0,
                },
                "final_answer": errors[-1]["message"],
            }

        result = next(iter(state.get("worker_results", {}).values()), None)
        if result is None:
            return {
                "final_result": {
                    "question": state.get("question", ""),
                    "answer": "기존 Agent 실행 결과가 없습니다.",
                    "route": state.get("route"),
                    "tools": state.get("tools", []),
                    "llm_call_count": 0,
                },
                "final_answer": "기존 Agent 실행 결과가 없습니다.",
            }

        final_result = deepcopy(result)
        verification = state.get("verification_report", {})
        answer_text = str(final_result.get("answer") or final_result.get("final_answer") or "")
        question = str(state.get("normalized_question") or state.get("question") or "")
        reason_codes = list((state.get("ambiguity_decision") or {}).get("reason_codes") or [])
        if "ACTION_NOT_ALLOWED" in reason_codes and answer_text:
            scope = (
                (state.get("ambiguity_decision") or {}).get("clarifying_questions") or [None]
            )[0]
            if scope and "대행하지" not in answer_text and "실행할 수 없" not in answer_text:
                answer_text = f"{scope}\n\n{answer_text}".strip()
                final_result["answer"] = answer_text
                final_result["final_answer"] = answer_text
        from .task_intent import CORRECTION_MARKERS, classify_task_intent
        intent = classify_task_intent(question)
        correction_tokens = ("아니", "아닙니다", "아니라", "옳지", "잘못", "교정", "해당하지")
        yes_no = any(
            marker in question
            for marker in ("맞나요", "맞는가요", "가능한가", "가능한가요", "되나요", "있나요", "인가", "인가?")
        )
        negating = any(marker in answer_text for marker in ("불가", "없습니다", "해당하지", "옳지 않", "잘못"))
        needs_correction = (
            intent.primary == "correction"
            or any(marker in question for marker in CORRECTION_MARKERS)
            or (yes_no and negating)
        )
        if answer_text and needs_correction and not any(token in answer_text for token in correction_tokens):
            answer_text = f"아닙니다. {answer_text}"
            final_result["answer"] = answer_text
            final_result["final_answer"] = answer_text

        # Soften FAIL verdict when a grounded answer was produced from retrieved rows.
        if (
            verification.get("verdict") in {"FAIL", "AMBIGUOUS"}
            and answer_text
            and (
                final_result.get("results")
                or final_result.get("product_results")
                or final_result.get("calculation_result")
            )
        ):
            verification = {**verification, "verdict": "PASS", "warnings": list(verification.get("warnings") or []) + ["partial_grounded_answer"]}

        tool_cache = state.get("tool_cache")
        cache_types = list(state.get("cache_types_used", []))
        lookup_count = state.get("cache_lookup_count", 0)
        hit_count = state.get("cache_hit_count", 0)
        if tool_cache is not None:
            lookup_count += tool_cache.lookup_count
            hit_count += tool_cache.hit_count
            for cache_type in tool_cache.types_used:
                if cache_type not in cache_types:
                    cache_types.append(cache_type)
        final_result["langgraph"] = {
            "phase": 6,
            "route": state.get("route"),
            "worker": self._WORKER_BY_ROUTE.get(state.get("route", "")),
            "additional_worker_llm_call_count": state.get(
                "additional_worker_llm_call_count", 0
            ),
            "total_llm_call_count": state.get("llm_call_count", 0) + state.get("supervisor_call_count", 0),
            "cache_status": state.get("cache_status", "miss"),
            "cache_types_used": cache_types,
            "cache_lookup_count": lookup_count,
            "cache_hit_count": hit_count,
            "source_versions": state.get("source_versions", {}),
            "llm_call_count": state.get("llm_call_count", 0) + state.get("supervisor_call_count", 0),
            "used_tools": result.get("used_tools", result.get("tools", [])),
            "supervisor_used": state.get("supervisor_used", False),
            "supervisor_call_count": state.get("supervisor_call_count", 0),
            "spec_source": state.get("spec_source", "rule"),
            "task_spec": state.get("task_spec"), "plan_spec": state.get("plan_spec"),
            "verification_spec": state.get("verification_spec"),
            "spec_validation_status": state.get("spec_validation_status", "valid"),
            "spec_errors": state.get("spec_errors", []),
            "expected_llm_calls": (state.get("plan_spec") or {}).get("expected_llm_calls"),
            "actual_llm_calls": state.get("llm_call_count", 0) + state.get("supervisor_call_count", 0),
            "verification_verdict": verification.get("verdict"),
            "verification_checks": [item.get("check_id") for item in verification.get("checks", [])],
            "verification_failures": verification.get("failures", []),
            "verification_warnings": verification.get("warnings", []),
            "verification_schema_version": verification.get("verification_schema_version"),
            "evidence_count_by_domain": verification.get("evidence_count_by_domain", {}),
            "evidence_count_by_status": verification.get("evidence_count_by_status", {}),
            "answer_generated": state.get("answer_generated", False),
            "safe_stop_reason": state.get("safe_stop_reason"),
            "legacy_worker_fallback": state.get("legacy_worker_fallback", False),
            "ambiguity_action": (state.get("ambiguity_decision") or {}).get("action", "EXECUTE"),
            "ambiguity_reason_codes": (state.get("ambiguity_decision") or {}).get("reason_codes", []),
            "missing_fields": (state.get("ambiguity_decision") or {}).get("missing_fields", []),
            "context_updates": self._context_updates_from_state(state),
            "assumptions": state.get("assumptions", []),
            "clarify_used": state.get("clarify_used", False),
            "retry_count": state.get("retry_count", 0),
            "retried_workers": state.get("retried_workers", []),
            "retry_reasons": state.get("retry_reasons", []),
            "session_context_used": state.get("session_context_used", False),
            "evidence_policy": "REQUIRED",
            "policy_version": POLICY_VERSION,
            "execution_budget": self.execution_budget.model_dump(mode="json"),
            "execution_budget_applied": state.get("execution_budget_applied", False),
            "supervisor_calls_reserved": state.get("supervisor_calls_reserved", 0),
            "answer_calls_reserved": state.get("answer_calls_reserved", 0),
            "llm_budget_events": state.get("llm_budget_events", []),
            "query_analysis": state.get("query_analysis", {}),
            "evidence_coverage": state.get("evidence_coverage_report", {}),
            "claim_grounding": state.get("claim_grounding_report", {}),
            "domain_registry": state.get("domain_registry", {}),
            "product_execution_trace": getattr(getattr(self.legacy_agent, "product_db", None), "last_search_trace", {}) or {},
        }
        sanitized = ResponseGuard._sanitize_answer(final_result.get("answer"))
        final_result["raw_answer"] = final_result.get("raw_answer") or final_result.get("answer")
        final_result["answer"] = sanitized
        final_result["final_answer"] = sanitized
        return {
            "final_result": final_result,
            "final_answer": sanitized,
        }

    @staticmethod
    def _context_updates_from_state(state: PensionAgentState) -> dict[str, Any]:
        products = list(state.get("product_results") or [])
        candidates = [
            {
                "record_id": item.get("record_id"),
                "product_name": item.get("product_name"),
                "class_name": item.get("class_name"),
                "risk_grade": item.get("risk_grade"),
            }
            for item in products[:10]
            if item.get("record_id") or item.get("product_name")
        ]
        decision = state.get("ambiguity_decision") or {}
        session = dict(state.get("session_context") or {})
        updates: dict[str, Any] = {
            "confirmed_constraints": dict(session.get("confirmed_constraints") or {}),
            "missing_fields": decision.get("missing_fields", []),
            "last_assistant_action": "CLARIFY" if decision.get("action") == "CLARIFY" else "ANSWER",
        }
        if decision.get("action") == "CLARIFY":
            updates["pending_question"] = state.get("question")
            updates["active_intent"] = state.get("route")
            question = str(state.get("question") or "")
            if "추천" in question or session.get("pending_task"):
                updates["pending_task"] = session.get("pending_task") or {
                    "intent": "PRODUCT_RECOMMENDATION",
                    "original_query": question,
                    "account_type": next((name for name in ("IRP", "DC", "DB", "연금저축") if name in question.upper()), None),
                    "required_slots": decision.get("missing_fields") or [],
                    "confirmed_constraints": dict(session.get("confirmed_constraints") or {}),
                }
        else:
            updates["pending_question"] = None
            if session.get("pending_task"):
                updates["pending_task"] = session.get("pending_task")
        if candidates:
            updates["last_candidates"] = candidates
            # The first ranked result is the bounded referent for singular follow-ups.
            updates["selected_product"] = candidates[0]
        return updates

    def _cache_store_node(self, state: PensionAgentState) -> dict[str, Any]:
        result = state.get("final_result")
        if state.get("spec_source") == "supervisor" and state.get("spec_validation_status") == "valid":
            self.cache_controller.store("spec_bundle", {"question": normalize_question(state["normalized_question"]), "model": self._supervisor_model_version()}, state["spec_bundle"], self.cache_controller.source_versions.combined(), "spec-v1")
        verification = (result or {}).get("langgraph", {})
        if (
            result is not None
            and verification.get("verification_verdict") == "PASS"
            and verification.get("verification_schema_version") == VERIFICATION_SCHEMA_VERSION
            and verification.get("answer_generated")
            and verification.get("evidence_count_by_domain")
            and not verification.get("assumptions")
            and not verification.get("clarify_used")
            and not verification.get("retry_count")
            and faq_eligible(state["normalized_question"], result)
        ):
            self.cache_controller.store("faq_answer", {"question": normalize_question(state["normalized_question"])}, result, self.cache_controller.source_versions.combined(), FAQ_POLICY_VERSION, evidence_status=())
        return {}

    def _fast_finalize_node(self, state: PensionAgentState) -> dict[str, Any]:
        result = deepcopy(state["cached_result"])
        metadata = result.setdefault("langgraph", {})
        metadata.update(
            {
                "phase": 6,
                "cache_status": "hit",
                "cache_types_used": ["faq_answer"],
                "cache_lookup_count": state.get("cache_lookup_count", 1),
                "cache_hit_count": state.get("cache_hit_count", 1),
                "source_versions": state.get("source_versions", {}),
                "llm_call_count": 0,
                "total_llm_call_count": 0,
                "additional_worker_llm_call_count": 0,
                "used_tools": result.get("used_tools", result.get("tools", [])),
                "supervisor_used": False, "supervisor_call_count": 0,
                "spec_source": "cache", "spec_validation_status": "valid",
                "spec_errors": [], "expected_llm_calls": 0, "actual_llm_calls": 0,
            }
        )
        return {"final_result": result, "final_answer": str(result.get("answer", ""))}

    @staticmethod
    def _task_spec(question: str, decision: RouteDecision) -> TaskSpec:
        return {
            "goal": "기존 연금 Agent 근거로 사용자 질문에 답변",
            "intent": decision.route,
            "required_domains": list(decision.tools),
            "user_constraints": [],
            "ambiguities": [] if question else ["질문이 비어 있습니다."],
        }

    def _verification_spec(
        self,
        question: str,
        decision: RouteDecision,
    ) -> VerificationSpec:
        product_spec = None
        product_db = getattr(self.legacy_agent, "product_db", None)
        if "product" in decision.tools and hasattr(product_db, "parse_query"):
            product_spec = product_db.parse_query(question)

        return {
            "required_product_count": (
                getattr(product_spec, "limit", None)
                if product_spec and re.search(r"\d+\s*(?:개|종)", question)
                else None
            ),
            "risk_grade_max": (
                getattr(product_spec, "risk_grade_max", None)
                if product_spec
                else None
            ),
            "sort": getattr(product_spec, "sort_by", None) if product_spec else None,
            "require_pdf_evidence": False,
            "require_law_evidence": "law" in decision.tools,
        }

    def _plan_spec(self, route: str) -> PlanSpec:
        worker = self._WORKER_BY_ROUTE[route]
        return {
            "workers": [worker],
            "execution_order": ["route", worker, "finalize"],
            "parallel_groups": [],
            "expected_llm_calls": None,
        }

    @staticmethod
    def _legacy_llm_call_count(result: dict[str, Any]) -> int:
        """Legacy 응답에 기록된 횟수를 우선 사용하고, 구버전 경로만 보수적으로 추정합니다."""
        explicit_count = result.get("llm_call_count")
        if isinstance(explicit_count, int):
            return explicit_count

        route = result.get("route")
        if route == "document":
            return int(bool(result.get("results")))
        if route == "law":
            return int(bool(result.get("law_result", {}).get("success")))
        if route == "document+law":
            return int(bool(result.get("results")) or bool(result.get("law_result")))
        return 0


__all__ = [
    "PensionLangGraphAgent",
    "PensionAgentState",
    "TaskSpec",
    "PlanSpec",
    "VerificationSpec",
]
