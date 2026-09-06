from __future__ import annotations

import time
import unittest

from chatbot.conversation_resolver import ConversationResolver
from chatbot.pension_ambiguity import SessionContext
from chatbot.query_router import QueryRouter
from chatbot.retriever import ChunkRetriever
from chatbot.paths import REPO_ROOT


class ConversationRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = ConversationResolver()
        self.session = {
            "session_id": "test",
            "pending_question_id": "q1",
            "confirmed_constraints": {},
            "missing_fields": ["risk_tolerance", "investment_horizon"],
            "pending_question": "IRP 상품 추천해줘",
            "active_intent": "product",
            "last_topic": "IRP",
            "last_assistant_action": "CLARIFY",
            "expires_at": time.time() + 600,
        }

    def test_example_followup_is_direct_and_no_evidence(self) -> None:
        result = self.resolver.resolve("예시 샘플을 줘봐", self.session)
        self.assertEqual(result.action, "DIRECT")
        self.assertEqual(result.evidence_policy, "NOT_REQUIRED")
        self.assertIn("15년", result.direct_answer or "")

    def test_short_slot_followup_reconstructs_pending_question(self) -> None:
        result = self.resolver.resolve("중간 정도, 10년", self.session)
        self.assertEqual(result.action, "EXECUTE")
        self.assertIn("IRP 상품 추천해줘", result.resolved_question)
        self.assertEqual(result.context_updates["missing_fields"], [])
        self.assertEqual(result.context_updates["confirmed_constraints"]["investment_horizon"], "10년")

    def test_procedure_followup_keeps_irp_topic(self) -> None:
        no_pending = dict(self.session)
        no_pending["missing_fields"] = []
        no_pending["pending_question"] = None
        result = self.resolver.resolve("가입하고 싶은데 어떻게 해?", no_pending)
        self.assertEqual(result.resolved_question, "IRP 가입하고 싶은데 어떻게 해?")

    def test_router_keeps_expected_domain_split(self) -> None:
        router = QueryRouter()
        self.assertEqual(router.decide("IRP 상품 추천해줘").route, "product")
        self.assertEqual(router.decide("IRP 계좌가 뭐야?").route, "document")
        self.assertEqual(router.decide("IRP 가입하고 싶은데 어떻게 해?").route, "document")

    def test_session_contract_accepts_conversation_fields(self) -> None:
        session = SessionContext.model_validate(self.session)
        self.assertEqual(session.last_topic, "IRP")

    def test_docs_retrieval_is_enterprise_primary(self) -> None:
        chunks = REPO_ROOT / "data" / "chatbot" / "chunks" / "chunks.jsonl"
        retriever = ChunkRetriever(chunks)
        results = retriever.retrieve("IRP 계좌가 뭐야?", top_k=1, source_group="docs")
        self.assertTrue(results)
        self.assertEqual(results[0]["source_priority"], "ENTERPRISE_PRIMARY")
        self.assertIn("IRP", results[0]["text"].upper())


if __name__ == "__main__":
    unittest.main()
