from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class QueryAnalysis:
    original_query: str
    intents: tuple[str, ...]
    entities: dict[str, list[str]]
    constraints: dict[str, Any]
    complexity: str
    needs_decomposition: bool
    subqueries: tuple[str, ...]
    missing_information: tuple[str, ...]
    required_evidence: tuple[str, ...]
    retrieval_queries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["intents"] = list(self.intents)
        value["subqueries"] = list(self.subqueries)
        value["missing_information"] = list(self.missing_information)
        value["required_evidence"] = list(self.required_evidence)
        value["retrieval_queries"] = list(self.retrieval_queries)
        return value


class AdaptiveQueryAnalyzer:
    """Deterministic first-pass analyzer for LangGraph routing.

    The analyzer intentionally does not answer the question.  It extracts
    coarse intent/entity/complexity signals so expensive LLM planning can be
    reserved for genuinely composite queries.  This keeps the competition
    path reproducible and HyperCLOVA-X-only for all LLM calls.
    """

    _ACCOUNT = ("IRP", "DC", "DB", "연금저축", "퇴직연금")
    _TAX = ("세액공제", "세금", "과세", "연금소득세", "퇴직소득세", "절세")
    _PRODUCT = ("상품", "펀드", "ETF", "위험등급", "보수", "수익률", "AUM", "잔고", "추천", "비교")
    _LAW = ("법", "법령", "규정", "한도", "요건", "중도인출", "수령", "가입", "운용")
    _CALC = ("얼마", "계산", "%", "퍼센트", "세액공제액", "부족분", "차이")

    def analyze(self, question: str) -> QueryAnalysis:
        q = " ".join((question or "").split())
        upper = q.upper()
        accounts = [name for name in self._ACCOUNT if (name in upper if name in {"IRP", "DC", "DB"} else name in q)]

        intents: list[str] = []
        if any(k in q for k in self._TAX):
            intents.append("tax")
        if any(k.lower() in q.lower() for k in self._PRODUCT):
            intents.append("product")
        if any(k in q for k in self._LAW):
            intents.append("rule")
        if any(k in q for k in self._CALC):
            intents.append("calculation")
        if "비교" in q or "다른" in q or "차이" in q:
            intents.append("comparison")
        if "추천" in q or "좋은" in q or "적합" in q:
            intents.append("conditional_recommendation")
        if not intents:
            intents.append("document_qa")
        intents = list(dict.fromkeys(intents))

        clauses = [c.strip() for c in re.split(r"[?!.]|(?:\s+그리고\s+)|(?:\s+또\s+)|(?:\s+하면서\s+)", q) if c.strip()]
        composite_signals = sum([
            len(intents) >= 2,
            len(accounts) >= 2,
            len(clauses) >= 2,
            "비교" in q,
            "둘" in q or "합쳐" in q or "동시에" in q,
        ])
        complexity = "high" if composite_signals >= 3 else ("medium" if composite_signals >= 1 else "low")
        needs_decomposition = complexity == "high"

        subqueries: list[str] = []
        if needs_decomposition:
            if len(accounts) >= 2 and ("비교" in q or "차이" in q or "다른" in q):
                for account in accounts:
                    subqueries.append(f"{account} 관련 질문 조건과 근거 확인: {q}")
            for intent in intents:
                if intent == "tax":
                    subqueries.append(f"세제·과세 규칙 확인: {q}")
                elif intent == "product":
                    subqueries.append(f"상품 속성·비교 근거 확인: {q}")
                elif intent == "rule":
                    subqueries.append(f"연금 제도·법령 규칙 확인: {q}")
            subqueries = list(dict.fromkeys(subqueries))[:5]

        missing: list[str] = []
        if "conditional_recommendation" in intents:
            if not re.search(r"\b(초저위험|저위험|중위험|고위험|공격적|안정적|보수적)\b", q):
                missing.append("위험 감내 수준")
            if not re.search(r"\d+\s*(년|개월)", q):
                missing.append("투자 기간")

        required_evidence: list[str] = []
        if "comparison" in intents and len(accounts) >= 2:
            required_evidence.extend([f"{account} 핵심 규칙/특성" for account in accounts])
        if "tax" in intents:
            required_evidence.extend(["세액공제 또는 과세 대상", "한도/세율/적용조건"])
        if "conditional_recommendation" in intents:
            required_evidence.extend(["상품 위험등급", "상품 비용/보수", "상품 성과/운용 특성"])
        if "rule" in intents:
            required_evidence.append("제도/법령 적용 요건")
        required_evidence = list(dict.fromkeys(required_evidence))

        retrieval_queries = [q]
        retrieval_queries.extend(subqueries)
        if "tax" in intents:
            retrieval_queries.append(f"{q} 공제 대상 한도 공제율 적용 조건")
        if "rule" in intents:
            retrieval_queries.append(f"{q} 요건 예외 절차")
        retrieval_queries = list(dict.fromkeys(retrieval_queries))[:7]

        constraints: dict[str, Any] = {}
        risk = re.search(r"([1-6])\s*등급", q)
        if risk:
            constraints["risk_grade"] = int(risk.group(1))
        age = re.search(r"(\d{2})\s*세", q)
        if age:
            constraints["age"] = int(age.group(1))

        return QueryAnalysis(
            original_query=q,
            intents=tuple(intents),
            entities={"accounts": accounts},
            constraints=constraints,
            complexity=complexity,
            needs_decomposition=needs_decomposition,
            subqueries=tuple(subqueries),
            missing_information=tuple(missing),
            required_evidence=tuple(required_evidence),
            retrieval_queries=tuple(retrieval_queries),
        )
