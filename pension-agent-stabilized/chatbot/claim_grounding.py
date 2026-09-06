from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any
from .numeric_facts import numeric_facts


@dataclass(frozen=True)
class ClaimGroundingReport:
    verdict: str
    numeric_claims: tuple[str, ...]
    unsupported_numeric_claims: tuple[str, ...]
    citation_like_markers: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("numeric_claims", "unsupported_numeric_claims", "citation_like_markers"):
            value[key] = list(value[key])
        return value


class ClaimGroundingVerifier:
    """Low-cost post-generation guard for numeric hallucinations.

    It is not an entailment model.  It catches the most damaging competition
    failure mode: new numbers appearing in the final answer that are absent
    from retrieved evidence/calculation output.  Semantic claim verification
    remains the responsibility of the existing RuleVerifier + evidence hub.
    """

    _NUM = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?\s*(?:%|원|만원|억원|년|개월|세|등급)?")
    _CITE = re.compile(r"(?:근거|출처|문서|법령|제\s*\d+\s*조|\[[^\]]+\])")

    @staticmethod
    def _norm_number(value: str) -> str:
        return re.sub(r"\s+", "", value.replace(",", ""))

    def verify(self, answer: str, evidence_texts: list[str], calculation_payload: dict[str, Any] | None = None) -> ClaimGroundingReport:
        answer = answer or ""
        facts = numeric_facts(answer)
        numeric = tuple(raw for raw, _, _ in facts)
        corpus = "\n".join(evidence_texts or [])
        if calculation_payload:
            # Only an independently verified result may be supplied by callers.
            value = calculation_payload.get("result")
            unit = {"KRW": "원", "PERCENT": "%"}.get(calculation_payload.get("unit"), calculation_payload.get("unit") or "")
            if value is not None:
                corpus += f"\n{value}{unit}"
        supported = {(value, unit) for _, value, unit in numeric_facts(corpus)}
        unsupported = tuple(raw for raw, value, unit in facts if (value, unit) not in supported)
        markers = tuple(dict.fromkeys(self._CITE.findall(answer)))
        verdict = "PASS" if not unsupported else "REVIEW"
        return ClaimGroundingReport(
            verdict=verdict,
            numeric_claims=numeric,
            unsupported_numeric_claims=unsupported,
            citation_like_markers=markers,
            reason="numeric_claims_grounded" if not unsupported else "answer_contains_numbers_not_seen_in_evidence",
        )
