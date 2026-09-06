from __future__ import annotations

from typing import Any

from .legal_guardrail import LegalRetrievalGuardrail
from .legal_store import LegalStore


class LegalRetriever:
    def __init__(self, store: LegalStore | None = None, guardrail: LegalRetrievalGuardrail | None = None) -> None:
        self.store = store or LegalStore()
        self.guardrail = guardrail or LegalRetrievalGuardrail()

    def retrieve_topic(self, topic: str, question: str = "", limit: int = 16) -> dict[str, Any]:
        scope = self.guardrail.route_scope(topic)
        if not scope["allowed"]:
            return {"success": False, "topic": topic, "message": "LEGAL_GUARDRAIL_DENY", "primary_sources": [], "references": []}
        rows = self.store.get_articles_for_sources(
            scope["source_keys"],
            allowed_articles=scope["allowed_articles"],
            query=question,
            limit=limit,
        )
        sources = [self._to_agent_source(row) for row in rows]
        return {
            "success": bool(sources),
            "topic": topic,
            "message": "LEGAL_DB_MATCH" if sources else "LEGAL_DB_EMPTY",
            "primary_sources": sources,
            "references": [],
            "retrieval_source": "legal_db",
        }

    def get_article(self, source_key: str, article_no: str) -> dict[str, Any] | None:
        if not self.guardrail.source_allowed(source_key, article_no):
            return None
        row = self.store.get_article(source_key, article_no)
        return self._to_agent_source(row) if row else None

    @staticmethod
    def _to_agent_source(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_type": "legal_db",
            "source_key": row.get("source_key"),
            "law_id": row.get("law_id"),
            "law_name": row.get("law_name"),
            "law_short_name": None,
            "ministry": None,
            "promulgation_date": row.get("promulgation_date"),
            "effective_date": row.get("effective_date"),
            "article_no": row.get("article_no"),
            "article_title": row.get("article_title"),
            "article_effective_date": row.get("effective_date"),
            "paragraphs": [{"paragraph_no": None, "text": row.get("article_text"), "items": []}],
            "origin_text": row.get("article_text"),
            "source_channel": row.get("source_channel"),
            "source_url": row.get("source_url"),
            "fetched_at": row.get("fetched_at"),
        }
