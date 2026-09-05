from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class CoverageReport:
    score: float
    required_domains: tuple[str, ...]
    covered_domains: tuple[str, ...]
    missing_domains: tuple[str, ...]
    evidence_count: int
    complete: bool
    reason: str
    required_facts: tuple[str, ...] = ()
    fact_coverage_score: float = 1.0
    uncovered_facts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("required_domains", "covered_domains", "missing_domains", "required_facts", "uncovered_facts"):
            value[key] = list(value[key])
        return value


class EvidenceCoverageChecker:
    """Conservative domain gate plus diagnostic fact-coverage estimate.

    Domain coverage is the hard safety gate. Fact coverage is diagnostic only:
    it helps the LangGraph planner identify where a follow-up retrieval should
    focus without blocking on brittle exact phrasing.
    """

    _ALIASES = {
        "document": {"document", "pdf"}, "product": {"product"},
        "law": {"law", "legal"}, "calculation": {"calculation"},
    }

    @staticmethod
    def _terms(text: str) -> set[str]:
        stop = {"관련", "핵심", "규칙", "특성", "또는", "적용", "조건", "상품", "제도", "법령"}
        return {t.lower() for t in re.findall(r"[A-Za-z]+|[가-힣]{2,}|\d+", text or "") if t.lower() not in stop}

    def check(self, required_domains: list[str], evidence: list[dict[str, Any]], result: dict[str, Any] | None = None, required_facts: list[str] | None = None) -> CoverageReport:
        required = tuple(dict.fromkeys(required_domains or []))
        covered: set[str] = set()
        for item in evidence or []:
            domain = str(item.get("domain") or item.get("source_type") or "").lower()
            for canonical, aliases in self._ALIASES.items():
                if domain in aliases:
                    covered.add(canonical)
        result = result or {}
        if result.get("results"): covered.add("document")
        if result.get("product_results"): covered.add("product")
        law_result = result.get("law_result") or result.get("law_results") or {}
        if isinstance(law_result, dict) and (law_result.get("success") or law_result.get("primary_sources")): covered.add("law")
        if result.get("calculation_result") or result.get("route") == "calculation": covered.add("calculation")

        missing = tuple(d for d in required if d not in covered)
        score = 1.0 if not required else (len(required) - len(missing)) / len(required)

        facts = tuple(dict.fromkeys(required_facts or []))
        corpus = " ".join(str(item.get("text") or item.get("content") or item) for item in evidence or []) + " " + str(result)
        corpus_terms = self._terms(corpus)
        uncovered: list[str] = []
        for fact in facts:
            terms = self._terms(fact)
            # diagnostic: at least half of discriminative terms should appear
            if terms and len(terms & corpus_terms) / len(terms) < 0.5:
                uncovered.append(fact)
        fact_score = 1.0 if not facts else (len(facts) - len(uncovered)) / len(facts)
        return CoverageReport(
            score=round(score, 4), required_domains=required, covered_domains=tuple(sorted(covered)),
            missing_domains=missing, evidence_count=len(evidence or []), complete=not missing,
            reason="all_required_domains_covered" if not missing else "missing_required_evidence_domains",
            required_facts=facts, fact_coverage_score=round(fact_score, 4), uncovered_facts=tuple(uncovered),
        )
