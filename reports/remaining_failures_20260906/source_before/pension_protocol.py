"""Phase 6 input/output safety boundary for the LangGraph public API."""
from __future__ import annotations

import re
import time
import uuid
from hashlib import sha256
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .display_units import INTERNAL_UNIT_ENUMS, PUBLIC_PRODUCT_SOURCE
from .pension_ambiguity import SessionContext
from .public_language import public_text, public_notice, public_assumption, source_label

MAX_QUESTION_LENGTH = 2000
MIN_TOP_K = 1
MAX_TOP_K = 10


class InputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    question_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_context: SessionContext | None = None
    requested_top_k: int = Field(default=5, ge=MIN_TOP_K, le=MAX_TOP_K)
    request_started_at: float = Field(default_factory=time.time)


class ExecutionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_supervisor_calls: int = 1
    max_answer_llm_calls: int = 1
    max_critic_calls: int = 0
    max_worker_retries: int = 1
    timeout_seconds: float = 60.0


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_code: str
    category: Literal["input", "budget", "guard", "system"]
    node: str
    retryable: bool
    safe_message: str
    internal_reference: str
    occurred_at: float = Field(default_factory=time.time)


class ResponseEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Literal["success", "clarify", "safe_stop", "input_error", "system_error"]
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_action: str
    question_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class InputHarness:
    def validate(self, payload: dict[str, Any]) -> InputRequest:
        request = InputRequest.model_validate(payload)
        if not request.question or not request.question.strip():
            raise ValueError("empty_question")
        if len(request.question.strip()) > MAX_QUESTION_LENGTH:
            raise ValueError("question_too_long")
        if request.session_context and not request.session_context.active(time.time()):
            raise ValueError("expired_session_context")
        return request.model_copy(update={"question": request.question.strip()})

    @staticmethod
    def error(question_id: str | None, code: str) -> dict[str, Any]:
        safe = {"empty_question": "질문을 입력해 주세요.", "question_too_long": "질문이 허용 길이를 초과했습니다.", "expired_session_context": "세션 정보가 만료되었습니다.", "validation_error": "입력 형식이 올바르지 않습니다."}.get(code, "입력을 확인해 주세요.")
        envelope = ErrorEnvelope(error_code=code, category="input", node="input_harness", retryable=False, safe_message=safe, internal_reference=str(uuid.uuid4()))
        return ResponseEnvelope(status="input_error", answer=safe, next_action="질문 또는 세션 정보를 수정해 다시 요청하세요.", question_id=question_id or str(uuid.uuid4()), metadata={"error": envelope.model_dump(mode="json")}).model_dump(mode="json")


class ResponseGuard:
    REASONING_LINE = re.compile(
        r"^\s*(?:Thought|Analysis|Reasoning|Chain of Thought|We need to|We have to|The user asks|"
        r"From the evidence|From doc|Also mention|Let's check|We should|We need|We have|"
        r"Thus we|Now we|structured DB evidence|PDF evidence|내부 메모|사고 과정|생각해보면)\b",
        re.IGNORECASE,
    )
    REASONING_INLINE = re.compile(
        r"(?:We need to|We have to|The user asks|Let's check|From the evidence|From doc|"
        r"Also mention|structured DB evidence|PDF evidence|\banalysis:|\breasoning:|\bthought:|"
        r"내부 메모|사고 과정)",
        re.IGNORECASE,
    )

    @staticmethod
    def _sanitize_answer(value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"```(?:analysis|reasoning)[\s\S]*?```", "", text, flags=re.IGNORECASE)
        kept = []
        for line in text.splitlines():
            if ResponseGuard.REASONING_LINE.match(line) or ResponseGuard.REASONING_INLINE.search(line):
                continue
            kept.append(line)
        text = "\n".join(kept).strip()
        text = re.sub(
            r"(?im)^\s*적용한 기본값:.*?(?:\n|$)",
            "",
            text,
        )
        text = text.replace("상품 PostgreSQL/Standard JSON 구조화 레코드", PUBLIC_PRODUCT_SOURCE)
        text = text.replace("PostgreSQL/Standard JSON", "상품 DB")
        text = re.sub(
            r"(?P<num>\d+(?:\.\d+)?)PERCENT_PER_YEAR",
            r"연 \g<num>%",
            text,
        )
        text = re.sub(
            r"(?P<num>\d+(?:\.\d+)?)PERCENT_PER_MONTH",
            r"월 \g<num>%",
            text,
        )
        text = re.sub(r"(?P<num>\d+(?:\.\d+)?)PERCENT\b", r"\g<num>%", text)
        text = re.sub(r"(?P<num>\d+(?:\.\d+)?)BASIS_POINT", r"\g<num>bp", text)
        text = public_text(text)
        for enum in INTERNAL_UNIT_ENUMS:
            text = text.replace(enum, "")
        text = re.sub(r"\n{3,}", "\n\n", text)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        korean_paragraphs = [
            paragraph for paragraph in paragraphs
            if len(re.findall(r"[가-힣]", paragraph)) >= 8
            and not ResponseGuard.REASONING_LINE.match(paragraph)
            and not re.search(r"\b(?:we need|we have|user asks|structured db|pdf evidence)\b", paragraph, re.IGNORECASE)
        ]
        if korean_paragraphs and len(korean_paragraphs) < len(paragraphs):
            text = "\n\n".join(korean_paragraphs)
        return public_text(text)

    @staticmethod
    def _assumptions_disclosed(answer: str, assumptions: list[dict[str, Any]]) -> bool:
        text = str(answer or "")
        if "다음" in text and "개" in text:
            return True
        if "보여드리겠습니다" in text:
            return True
        for item in assumptions:
            field = str(item.get("field") or "")
            value = str(item.get("value") or "")
            if field and field in text:
                return True
            if field and public_text(field) in text:
                return True
            if value and value in text:
                return True
            if value and public_text(value) in text:
                return True
        return False

    def guard(self, result: dict[str, Any], question_id: str) -> dict[str, Any]:
        meta = result.get("langgraph", {})
        route = result.get("route", "")
        verdict = meta.get("verification_verdict")
        clarify = bool(meta.get("clarify_used"))
        reason_codes = list(meta.get("ambiguity_reason_codes") or [])
        answer_text = self._sanitize_answer(result.get("final_answer") or result.get("answer"))
        # Capability refusals are successful explanations, not evidence failures.
        action_refused = "ACTION_NOT_ALLOWED" in reason_codes and bool(answer_text)
        safe_stop = bool(meta.get("safe_stop_reason")) and not clarify and not action_refused
        sources = self._sources(result)
        assumptions = meta.get("assumptions", [])
        if clarify:
            status, next_action = "clarify", "필요한 정보를 제공한 뒤 다시 요청하세요."
        elif action_refused:
            status, next_action = "success", "상품 정보나 제도 안내가 필요하면 이어서 질문해 주세요."
        elif safe_stop:
            status, next_action = "safe_stop", "근거 또는 조건을 확인한 뒤 다시 요청하세요."
        elif verdict in {"FAIL", "AMBIGUOUS"} and answer_text and any(
            item.get("domain") in {"document", "product", "calculation", "law"} for item in sources
        ):
            # Prefer grounded partial answers over wiping with system_error.
            status, next_action = "success", "추가 조건이 있으면 알려주세요."
        elif verdict in {"FAIL", "AMBIGUOUS"}:
            status, next_action = "safe_stop", "근거 또는 조건을 확인한 뒤 다시 요청하세요."
        else:
            status, next_action = "success", "추가 조건이 있으면 알려주세요."
        failure = None
        evidence_policy = str(meta.get("evidence_policy") or "REQUIRED")
        if status == "success" and not action_refused:
            has_document = any(item.get("domain") == "document" for item in sources)
            has_product = any(item.get("domain") == "product" for item in sources)
            has_law = any(item.get("domain") == "law" for item in sources)
            has_calc = any(item.get("domain") == "calculation" for item in sources)
            if not answer_text:
                failure = "verification_or_answer_missing"
            elif verdict not in {"PASS", "AMBIGUOUS"} and not (has_document or has_product or has_calc):
                failure = "verification_or_answer_missing"
            elif evidence_policy == "REQUIRED" and not sources:
                failure = "sources_missing"
            elif "product" in route and not has_product and not has_document:
                failure = "product_source_missing"
            elif "law" in route and not has_law and not has_document:
                # Enterprise document hits can carry the institutional answer when
                # formal law rows were incomplete; do not wipe the user answer.
                failure = "law_source_missing"
            elif "calculation" in route and not has_calc and "document" not in route:
                failure = "calculation_source_missing"
            elif assumptions and not ResponseGuard._assumptions_disclosed(answer_text, assumptions):
                failure = "assumption_not_disclosed"
            elif (int(meta.get("llm_call_count", 0)) - int(meta.get("supervisor_call_count", 0))) > 1 or int(meta.get("supervisor_call_count", 0)) > 1:
                failure = "llm_budget_exceeded"
        if failure:
            error = ErrorEnvelope(error_code=failure, category="guard", node="response_guard", retryable=False, safe_message="검증된 근거를 갖춘 응답을 만들지 못했습니다.", internal_reference=str(uuid.uuid4()))
            return ResponseEnvelope(status="system_error", answer=error.safe_message, limitations=[public_notice(failure)], next_action="잠시 후 다시 요청하거나 조건을 구체적으로 알려주세요.", question_id=question_id, metadata={"response_guard_status": "blocked", "error": error.model_dump(mode="json")}).model_dump(mode="json")
        public_sources = [
            {
                "label": source_label(item),
                "source_file": item.get("source_file"),
                "source_page": item.get("source_page"),
                # Keep domain for evaluator/observability; UI uses label only.
                "domain": item.get("domain"),
            }
            for item in sources
            if item.get("source_file") or item.get("domain")
        ]
        return ResponseEnvelope(
            status=status,
            answer=answer_text,
            sources=public_sources,
            evidence_summary={"domain": meta.get("evidence_count_by_domain", {}), "status": meta.get("evidence_count_by_status", {})},
            assumptions=[public_assumption(item) for item in assumptions],
            limitations=list(dict.fromkeys(public_notice(item) for item in meta.get("verification_failures", []) + meta.get("verification_warnings", []))),
            next_action=next_action,
            question_id=question_id,
            metadata={
                "response_guard_status": "passed",
                "verification_failures": meta.get("verification_failures", []),
                "verification_warnings": meta.get("verification_warnings", []),
                "route": route,
                "cache_status": meta.get("cache_status"),
                "llm_call_count": meta.get("llm_call_count", 0),
                "missing_fields": meta.get("missing_fields", []),
                "ambiguity_action": meta.get("ambiguity_action"),
                "evidence_policy": evidence_policy,
                "context_updates": meta.get("context_updates", {}),
                "final_answer": answer_text,
                "raw_answer": result.get("raw_answer"),
                "query_spec": meta.get("product_query_spec") or result.get("product_query_spec"),
                "candidate_ids": [
                    item.get("product_id") for item in sources if item.get("product_id")
                ],
                "raw_units": meta.get("raw_units"),
                "normalization_status": meta.get("normalization_status"),
                "internal_sources": sources,
            },
        ).model_dump(mode="json")

    @staticmethod
    def _sources(result: dict[str, Any]) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for item in result.get("results", []) or []:
            sources.append({"domain": "document", "source_file": item.get("filename"), "source_page": item.get("location"), "evidence_id": item.get("chunk_id") or item.get("document_id")})
        for item in result.get("product_results", []) or []:
            sources.append({"domain": "product", "source_file": item.get("source_file"), "source_page": (item.get("source_pages") or [None])[0], "evidence_id": (item.get("evidence_ids") or [None])[0], "product_id": item.get("record_id") or item.get("product_id")})
        for item in result.get("pdf_evidence") or []:
            if item.get("chunks") or item.get("source_file"):
                sources.append({"domain": "document", "source_file": item.get("source_file"), "source_page": item.get("source_page"), "evidence_id": f"pdf:{item.get('source_file')}:{item.get('source_page')}"})
        for item in (result.get("law_result") or {}).get("primary_sources", []) or []:
            sources.append({"domain": "law", "source_file": item.get("law_name"), "source_page": None, "evidence_id": f"{item.get('law_name')}:{item.get('article_no')}", "source_locator": item.get("article_no")})
        calculation = result.get("calculation_result") or {}
        if calculation:
            sources.append({"domain": "calculation", "source_file": None, "source_page": None, "evidence_id": f"calculation:{calculation.get('formula_id')}:{calculation.get('formula_version')}", "source_locator": calculation.get("formula_id"), "formula_version": calculation.get("formula_version")})
        return sources


class AuditBackend(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


class InMemoryAuditBackend:
    def __init__(self) -> None: self.records: list[dict[str, Any]] = []
    def write(self, record: dict[str, Any]) -> None: self.records.append(record)


def audit_record(question: str, question_id: str, result: dict[str, Any], response: dict[str, Any], started_at: float) -> dict[str, Any]:
    meta = result.get("langgraph", {})
    now = time.time()
    return {"question_id": question_id, "question_hash": sha256(question.encode("utf-8")).hexdigest(), "question_length": len(question), "route": result.get("route"), "cache_status": meta.get("cache_status"), "spec_source": meta.get("spec_source"), "supervisor_call_count": meta.get("supervisor_call_count", 0), "answer_llm_call_count": int(bool(meta.get("answer_generated"))), "worker_call_counts": {name: 1 for name in meta.get("used_tools", [])}, "retry_count": meta.get("retry_count", 0), "clarify_used": meta.get("clarify_used", False), "assumptions_used": bool(meta.get("assumptions")), "verification_verdict": meta.get("verification_verdict"), "response_guard_status": response.get("metadata", {}).get("response_guard_status"), "source_versions": meta.get("source_versions", {}), "used_tools": meta.get("used_tools", []), "node_latencies_ms": {}, "total_latency_ms": round((now - started_at) * 1000, 3), "error_codes": [response.get("metadata", {}).get("error", {}).get("error_code")] if response.get("metadata", {}).get("error") else [], "created_at": now}
