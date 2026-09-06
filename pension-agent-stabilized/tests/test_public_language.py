import unittest
from chatbot.public_language import public_text, public_notice, public_assumption, FIELDS, NOTICES
from chatbot.pension_protocol import ResponseGuard

class PublicLanguageTests(unittest.TestCase):
    def test_clarification_fields_are_readable(self):
        for field in ["risk_tolerance", "investment_horizon", "holding_product_name", "account_type", "personal_legal_facts", "product_reference"]:
            self.assertEqual(public_text(field), FIELDS[field])

    def test_preserves_financial_facts_and_filenames(self):
        self.assertEqual(public_text(0), "0")
        self.assertIn("13.2%", public_text("13.2 PERCENT"))
        self.assertIn("900", public_text("900 KRW"))
        self.assertNotIn("KRW", public_text("900 KRW"))
        text = "IRP TDF ETF 900 13.2% 1188000 report_IRP_2026.pdf `risk_tolerance`"
        answer = public_text(text)
        for fact in ["IRP", "TDF", "ETF", "900", "13.2%", "1188000", "report_IRP_2026.pdf"]:
            self.assertIn(fact, answer)
        self.assertNotIn("risk_tolerance", answer)
        self.assertNotIn("`", answer)

    def test_notices_and_assumptions(self):
        self.assertEqual(public_notice("product_count"), NOTICES["product_count"])
        self.assertNotIn("unknown_check", public_notice("unknown_check"))
        item = {"field":"product_limit", "value":5, "reason":"default_policy"}
        mapped = public_assumption(item)
        self.assertEqual(mapped["field"], item["field"])
        self.assertIn("5", mapped["label"])
        self.assertNotIn("product_limit", mapped["label"])
        self.assertNotIn("label", item)

    def test_guard_preserves_diagnostics_but_labels_public_fields(self):
        raw = {"answer": "risk_tolerance", "route":"document", "results":[{"filename":"guide.pdf", "location":3}],
               "langgraph":{"verification_verdict":"PASS", "verification_warnings":["source_versions"], "llm_call_count":0}}
        response = ResponseGuard().guard(raw, "test")
        self.assertEqual(response["answer"], FIELDS["risk_tolerance"])
        self.assertEqual(response["limitations"], [NOTICES["source_versions"]])
        self.assertEqual(response["metadata"]["verification_warnings"], ["source_versions"])
        self.assertEqual(response["sources"][0]["label"], "guide.pdf")

    def test_guard_failure_does_not_expose_error_code(self):
        response = ResponseGuard().guard({"answer":"IRP", "langgraph":{"verification_verdict":"FAIL"}}, "test")
        self.assertNotIn("verification_or_answer_missing", response["answer"])
        response = ResponseGuard().guard({"answer":"IRP", "langgraph":{"verification_verdict":"PASS"}}, "test")
        self.assertEqual(response["limitations"], [NOTICES["sources_missing"]])
        self.assertEqual(response["metadata"]["error"]["error_code"], "sources_missing")

if __name__ == "__main__":
    unittest.main()
