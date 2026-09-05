from __future__ import annotations

import os
from typing import Any

from .law_api_client import LawAPIClient
from .law_reference_resolver import LawReferenceResolver
from .legal_retriever import LegalRetriever


class LawTool:
    """DB-first legal retrieval.

    Normal chat serving reads the preloaded legal DB.  Direct API fallback is off
    by default and is intended only as a temporary operational escape hatch.
    Periodic refresh belongs to scripts/sync_legal_db.py.
    """

    def __init__(self):
        self.retriever = LegalRetriever()
        self.resolver = LawReferenceResolver()
        self._api_client: LawAPIClient | None = None
        self.allow_api_fallback = os.getenv("LAW_QUERY_FALLBACK_API", "0") == "1"

    @property
    def api_client(self) -> LawAPIClient:
        if self._api_client is None:
            self._api_client = LawAPIClient()
        return self._api_client

    def search(self, question: str) -> dict[str, Any]:
        question = question.strip()
        q_lower = question.lower()

        is_irp = any(k in q_lower for k in ("irp", "개인형퇴직연금", "개인형 퇴직연금"))
        if is_irp:
            if any(k in question for k in ("중도인출", "중도 인출", "인출")):
                return self._irp_withdrawal()
            return self._article_or_topic("IRP", "RETIREMENT_BENEFIT_ACT", "제24조")

        if any(k in question for k in ("DB형", "확정급여형", "확정급여")):
            return self._article_or_topic("DB", "RETIREMENT_BENEFIT_ACT", "제13조")

        if any(k in question for k in ("DC형", "확정기여형", "확정기여")):
            return self._article_or_topic("DC", "RETIREMENT_BENEFIT_ACT", "제19조")

        if any(k in question for k in ("세액공제", "연금계좌", "연금저축")):
            result = self.retriever.retrieve_topic("TAX_CREDIT", question, limit=8)
            if result.get("success"):
                return result

        return {
            "success": False,
            "topic": None,
            "message": "현재 연결된 법률 주제를 찾지 못했습니다.",
            "primary_sources": [],
            "references": [],
            "retrieval_source": "legal_db",
        }

    def _article_or_topic(self, topic: str, source_key: str, article_no: str) -> dict[str, Any]:
        article = self.retriever.get_article(source_key, article_no)
        if article:
            return {
                "success": True,
                "topic": topic,
                "message": "LEGAL_DB_MATCH",
                "primary_sources": [article],
                "references": [],
                "retrieval_source": "legal_db",
            }
        if self.allow_api_fallback:
            law_name = self.retriever.guardrail.data["source_registry"][source_key]["law_name"]
            api_article = self.api_client.get_article(law_name, article_no.replace("제", "").replace("조", ""))
            if api_article:
                return {
                    "success": True,
                    "topic": topic,
                    "message": "LAW_API_FALLBACK_MATCH",
                    "primary_sources": [api_article],
                    "references": [],
                    "retrieval_source": "law_api_fallback",
                }
        return {
            "success": False,
            "topic": topic,
            "message": "LEGAL_DB_EMPTY",
            "primary_sources": [],
            "references": [],
            "retrieval_source": "legal_db",
        }

    def _irp_withdrawal(self) -> dict[str, Any]:
        main = self.retriever.get_article("RETIREMENT_BENEFIT_ACT", "제24조")
        decree = self.retriever.get_article("RETIREMENT_BENEFIT_DECREE", "제18조")
        sources = [item for item in (main, decree) if item]
        if len(sources) == 2:
            return {
                "success": True,
                "topic": "IRP_WITHDRAWAL",
                "message": "LEGAL_DB_MATCH",
                "primary_sources": sources,
                "references": [],
                "retrieval_source": "legal_db",
            }
        if self.allow_api_fallback:
            api_main = self.api_client.get_article("근로자퇴직급여 보장법", "24")
            api_decree = self.api_client.get_article("근로자퇴직급여 보장법 시행령", "18")
            api_sources = [item for item in (api_main, api_decree) if item]
            if len(api_sources) == 2:
                return {
                    "success": True,
                    "topic": "IRP_WITHDRAWAL",
                    "message": "LAW_API_FALLBACK_MATCH",
                    "primary_sources": api_sources,
                    "references": [],
                    "retrieval_source": "law_api_fallback",
                }
        return {
            "success": False,
            "topic": "IRP_WITHDRAWAL",
            "message": "LEGAL_DB_EMPTY",
            "primary_sources": sources,
            "references": [],
            "retrieval_source": "legal_db",
        }
