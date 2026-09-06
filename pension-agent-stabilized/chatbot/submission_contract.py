"""think_trace is an execution summary, never hidden reasoning.

These explicit field types must be checked against the official contest schema.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from .public_security import public_payload


class SubmissionResponse(BaseModel):
    question_id: str
    question: str
    retrieved_context: list[dict] = Field(default_factory=list)
    think_trace: str
    answer: str


def to_submission(question: str, envelope: dict) -> dict:
    metadata = envelope.get('metadata') or {}
    summary = '조회 경로: {route}; 처리 상태: {status}; 검증: {verification}; 출처: {count}건'.format(
        route=metadata.get('route') or '미실행', status=envelope.get('status', 'system_error'),
        verification=metadata.get('verification_status') or '미확인', count=len(envelope.get('sources') or []))
    contract = SubmissionResponse(question_id=envelope['question_id'], question=question,
        retrieved_context=list(envelope.get('sources') or []), think_trace=summary, answer=envelope.get('answer') or '')
    return public_payload({**envelope, **contract.model_dump()})
