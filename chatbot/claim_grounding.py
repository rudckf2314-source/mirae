from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


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
        numeric = tuple(dict.fromkeys(self._NUM.findall(answer)))
        corpus = "\n".join(evidence_texts or [])
        if calculation_payload:
            corpus += "\n" + str(calculation_payload)
        normalized_corpus = self._norm_number(corpus)
        unsupported = tuple(n for n in numeric if self._norm_number(n) not in normalized_corpus)
        markers = tuple(dict.fromkeys(self._CITE.findall(answer)))
        verdict = "PASS" if not unsupported else "REVIEW"
        return ClaimGroundingReport(
            verdict=verdict,
            numeric_claims=numeric,
            unsupported_numeric_claims=unsupported,
            citation_like_markers=markers,
            reason="numeric_claims_grounded" if not unsupported else "answer_contains_numbers_not_seen_in_evidence",
        )
