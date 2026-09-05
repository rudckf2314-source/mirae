from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .risk_policy import qualitative_risk_constraint_text


EXAMPLE_MARKERS = ("예시", "샘플", "어떻게 말", "어떻게 답", "예를 들어")
PROCEDURE_MARKERS = ("가입", "개설", "신청", "어떻게 해", "어떻게 하면")
ACCOUNT_TOPICS = ("IRP", "DC", "DB", "연금저축")
DEICTIC_PRODUCT_MARKERS = ("그 상품", "이 상품", "해당 상품")
CANDIDATE_MARKERS = ("그중", "그 중", "추천한 상품", "후보 중")

# Qualitative slots stay semantic. Explicit "N등급 이하" is parsed separately
# by ProductQuerySpec and is not synthesized from risk_tolerance.


@dataclass
class ConversationResolution:
    action: str = "EXECUTE"  # EXECUTE | DIRECT
    resolved_question: str = ""
    direct_answer: str | None = None
    evidence_policy: str = "REQUIRED"
    context_updates: dict[str, Any] = field(default_factory=dict)


def _risk_tolerance(text: str) -> str | None:
    q = text.replace(" ", "")
    if any(token in q for token in ("안정형", "보수적", "원금손실싫", "낮은위험")):
        return "conservative"
    if any(token in q for token in ("중립형", "중간", "보통위험", "적당한위험")):
        return "moderate"
    if any(token in q for token in ("적극형", "공격적", "높은위험", "변동감수")):
        return "aggressive"
    return None


def _investment_horizon(text: str) -> str | None:
    match = re.search(r"(\d{1,2})\s*년", text)
    if match:
        return f"{match.group(1)}년"
    if "단기" in text:
        return "단기"
    if "중기" in text:
        return "중기"
    if "장기" in text:
        return "장기"
    return None


def _topic_from_text(text: str) -> str | None:
    upper = text.upper()
    for topic in ACCOUNT_TOPICS:
        if topic.upper() in upper:
            return topic
    return None


class ConversationResolver:
    """Resolve short follow-ups before routing/retrieval.

    This module is deliberately deterministic. It does not invent product facts
    and never bypasses evidence for factual product/legal answers. Only
    conversational examples/clarifications may return directly without evidence.
    """

    def resolve(self, question: str, session: dict[str, Any] | None) -> ConversationResolution:
        q = question.strip()
        session = session or {}
        missing = list(session.get("missing_fields") or [])
        confirmed = dict(session.get("confirmed_constraints") or {})
        pending = str(session.get("pending_question") or "").strip()
        last_topic = session.get("last_topic")

        # A request for an example after a clarification request is not a new
        # evidence-bearing finance question. Answer it directly and preserve the
        # pending task.
        if missing and any(marker in q for marker in EXAMPLE_MARKERS):
            examples: list[str] = []
            if "risk_tolerance" in missing and "investment_horizon" in missing:
                examples = [
                    "예: '원금 변동은 어느 정도 감수할 수 있고, 은퇴까지 약 15년 남았습니다.'",
                    "예: '손실 가능성을 최대한 낮추고 싶고, 5년 정도 운용할 예정입니다.'",
                ]
            elif "risk_tolerance" in missing:
                examples = ["예: '중간 정도의 변동성까지는 감수할 수 있습니다.'"]
            elif "investment_horizon" in missing:
                examples = ["예: '연금 수령까지 약 10년 남았습니다.'"]
            else:
                examples = ["예: 'IRP 계좌에서 위험등급 3등급 이하 상품 3개를 비교해 주세요.'"]
            answer = "이렇게 알려주시면 됩니다.\n" + "\n".join(examples)
            return ConversationResolution(
                action="DIRECT",
                resolved_question=q,
                direct_answer=answer,
                evidence_policy="NOT_REQUIRED",
                context_updates={"last_assistant_action": "EXAMPLE_RESPONSE"},
            )

        # Fill pending recommendation slots from a short follow-up such as
        # "중간 정도, 10년" and reconstruct the original request for routing.
        if pending and missing:
            risk = _risk_tolerance(q)
            horizon = _investment_horizon(q)
            if risk:
                confirmed["risk_tolerance"] = risk
            if horizon:
                confirmed["investment_horizon"] = horizon
            still_missing = [
                field for field in missing
                if not confirmed.get(field)
            ]
            if len(still_missing) < len(missing):
                details = []
                if confirmed.get("risk_tolerance"):
                    details.append(f"위험성향={confirmed['risk_tolerance']}")
                if confirmed.get("investment_horizon"):
                    details.append(f"투자기간={confirmed['investment_horizon']}")
                resolved = pending
                if details:
                    resolved += ". 추가 사용자 조건: " + ", ".join(details)
                risk_hint = qualitative_risk_constraint_text(str(confirmed.get("risk_tolerance") or "") or None)
                if risk_hint and risk_hint not in resolved:
                    resolved += f". {risk_hint}"
                account = _topic_from_text(pending) or last_topic
                pending_task = {
                    "intent": "PRODUCT_RECOMMENDATION",
                    "original_query": pending,
                    "account_type": account,
                    "required_slots": ["risk_tolerance", "investment_horizon"],
                    "confirmed_constraints": dict(confirmed),
                }
                return ConversationResolution(
                    action="EXECUTE",
                    resolved_question=resolved,
                    evidence_policy="REQUIRED",
                    context_updates={
                        "confirmed_constraints": confirmed,
                        "missing_fields": still_missing,
                        "pending_task": pending_task,
                        "last_topic": account or last_topic,
                    },
                )

        # Hypothetical recommendation examples are conversational guidance, not
        # evidence-bearing factual recommendations.
        if any(marker in q for marker in ("예시로", "가정해서", "가상의")) and any(marker in q for marker in ("추천", "투자자")):
            return ConversationResolution(
                action="DIRECT",
                resolved_question=q,
                direct_answer=(
                    "예시로 안정형 투자자라면 원금 변동을 낮추는 방향에서 "
                    "위험등급이 낮고 채권 비중이 높은 후보를 먼저 비교할 수 있습니다. "
                    "이는 실제 상품 추천이 아니라 예시 시나리오입니다. 실제 추천에는 투자기간과 감내 위험을 확인합니다."
                ),
                evidence_policy="NOT_REQUIRED",
                context_updates={"last_assistant_action": "HYPOTHETICAL_EXAMPLE"},
            )

        candidates = list(session.get("last_candidates") or [])
        selected = session.get("selected_product")

        # Resolve a singular product pronoun only from bounded session state.
        if any(marker in q for marker in DEICTIC_PRODUCT_MARKERS) and selected:
            name = str(selected.get("product_name") or "").strip()
            if name:
                resolved = q
                for marker in DEICTIC_PRODUCT_MARKERS:
                    resolved = resolved.replace(marker, name)
                return ConversationResolution(
                    action="EXECUTE", resolved_question=resolved, evidence_policy="REQUIRED",
                    context_updates={"selected_product": selected},
                )

        # Restrict follow-up ranking/comparison to the previous recommendation set.
        if candidates and any(marker in q for marker in CANDIDATE_MARKERS):
            record_ids = [str(item.get("record_id") or "") for item in candidates if item.get("record_id")]
            scoped = q + (" [후보ID:" + ";;".join(record_ids) + "]" if record_ids else "")
            return ConversationResolution(
                action="EXECUTE", resolved_question=scoped, evidence_policy="REQUIRED",
                context_updates={"last_candidates": candidates},
            )

        # Elliptical procedure follow-up: "가입하고 싶은데 어떻게 해?" after an
        # IRP explanation should retain the last topic instead of searching a
        # content-free generic sentence.
        if last_topic and not _topic_from_text(q) and any(marker in q for marker in PROCEDURE_MARKERS):
            return ConversationResolution(
                action="EXECUTE",
                resolved_question=f"{last_topic} {q}",
                evidence_policy="REQUIRED",
            )

        return ConversationResolution(
            action="EXECUTE",
            resolved_question=q,
            evidence_policy="REQUIRED",
            context_updates={"last_topic": _topic_from_text(q) or last_topic},
        )
