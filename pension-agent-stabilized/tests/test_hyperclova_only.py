import os
import unittest
from unittest.mock import patch

import httpx

from chatbot.hyperclova_client import HyperClovaLLM
from chatbot.llm_provider import HyperClovaProviderAdapter
from chatbot.pension_supervisor import HyperClovaSpecificationSupervisor


class HyperClovaOnlyTests(unittest.TestCase):
    def test_structured_consumers_use_clova_native_api(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"result": {"message": {"content": '{"ok":true}'}}})

        client = httpx.Client(transport=httpx.MockTransport(handle))
        with patch.dict(os.environ, {"LLM_PROVIDER": "nvidia", "COMPETITION_MODE": "0"}), patch(
            "chatbot.hyperclova_client.httpx.Client", return_value=client
        ):
            llm = HyperClovaLLM(api_key="test-key", model="HCX-005",
                               base_url="https://clovastudio.stream.ntruss.com")
            provider = HyperClovaProviderAdapter(llm)
            self.assertEqual(provider.structured("classify", {}), {"ok": True})
            self.assertEqual(str(requests[0].url),
                             "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-005")
            self.assertEqual(requests[0].headers["Authorization"], "Bearer test-key")
        with patch.object(llm, "structured", return_value={"plan": []}) as structured:
            self.assertEqual(HyperClovaSpecificationSupervisor(llm).analyze({}), {"plan": []})
            structured.assert_called_once()
        with self.assertRaises(ValueError):
            provider.structured_for_model("classify", {}, "another-provider/model")

    def test_role_models_are_independent(self):
        from chatbot.model_policy import model_for_role
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(model_for_role("supervisor"), "HCX-007")
            self.assertEqual(model_for_role("answer"), "HCX-005")
            self.assertEqual(model_for_role("normalizer"), "HCX-DASH-002")
            self.assertEqual(model_for_role("extraction"), "HCX-005")
        with patch.dict(os.environ, {"CLOVA_ANSWER_MODEL": "HCX-DASH-002"}):
            self.assertEqual(model_for_role("answer"), "HCX-DASH-002")
        with patch.dict(os.environ, {"CLOVA_ANSWER_MODEL": "foreign-model"}):
            with self.assertRaises(ValueError):
                model_for_role("answer")

    def test_hcx007_uses_completion_budget_without_thinking(self):
        import json
        sent = []
        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"result": {"message": {"content": '{"ok":true}'}}})
        client = httpx.Client(transport=httpx.MockTransport(handle))
        with patch("chatbot.hyperclova_client.httpx.Client", return_value=client):
            self.assertEqual(HyperClovaLLM(api_key="test-key", model="HCX-007").structured("plan", {}), {"ok": True})
        self.assertEqual(sent[0]["thinking"], {"effort": "none"})
        self.assertEqual(sent[0]["maxCompletionTokens"], 1600)
        self.assertNotIn("maxTokens", sent[0])
        self.assertNotIn("stop", sent[0])

    def test_missing_key_fails_without_alternative_provider(self):
        with self.assertRaises(RuntimeError):
            HyperClovaLLM(api_key="")

    def test_api_error_propagates_without_fallback(self):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(503)

        client = httpx.Client(transport=httpx.MockTransport(handle))
        with patch("chatbot.hyperclova_client.httpx.Client", return_value=client):
            with self.assertRaises(httpx.HTTPStatusError):
                HyperClovaLLM(api_key="test-key").generate_from_evidence("question", "evidence")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
