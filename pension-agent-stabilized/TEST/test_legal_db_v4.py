from decimal import Decimal

from chatbot.calculation_gateway import tax_credit_spec
from chatbot.calculation_worker import CalculationWorker, PolicyRule, CalculationResult
from chatbot.calculation_verifier import CalculationRuleVerifier
from chatbot.legal_guardrail import LegalRetrievalGuardrail
from chatbot.legal_retriever import LegalRetriever
from chatbot.legal_store import LegalStore
from chatbot.pension_evidence import EvidenceHub
from chatbot.query_router import QueryRouter, product_search_hints
from chatbot.tax_policy_repository import TaxPolicyRepository


def test_guardrail_denies_unknown_topic():
    scope = LegalRetrievalGuardrail().route_scope("TOTALLY_UNKNOWN")
    assert scope["allowed"] is False


def test_seeded_tax_article_and_policy_are_retrievable():
    store = LegalStore()
    policy = TaxPolicyRepository(store).pension_tax_credit(2026)
    assert policy is not None
    assert policy.combined_credit_base_limit == Decimal("9000000")
    assert policy.pension_savings_credit_base_limit == Decimal("6000000")
    article = LegalRetriever(store).get_article("INCOME_TAX_ACT", "제59조의3")
    assert article and article["law_name"] == "소득세법"


def test_tax_credit_limit_is_deterministic_and_verifiable():
    store = LegalStore()
    policy = TaxPolicyRepository(store).pension_tax_credit(2026)
    assert policy is not None
    spec = tax_credit_spec("연금저축이랑 IRP에 넣으면 세액공제 얼마까지 되나요? 다 합쳐서요.", policy_year=2026)
    assert not isinstance(spec, dict)
    rule = PolicyRule(
        formula_id=policy.formula_id, version=policy.version, source=policy.source_type,
        tax_credit_rate=policy.standard_rate, lower_income_tax_credit_rate=policy.lower_income_rate,
        contribution_limit=policy.combined_credit_base_limit, pension_savings_limit=policy.pension_savings_credit_base_limit,
        gross_salary_threshold=policy.gross_salary_threshold, comprehensive_income_threshold=policy.comprehensive_income_threshold,
        evidence_source_key=policy.evidence_source_key, evidence_article_no=policy.evidence_article_no,
    )
    result = CalculationWorker({2026: rule}).run(spec)
    assert isinstance(result, CalculationResult)
    assert result.result == "9000000"
    article = LegalRetriever(store).get_article(policy.evidence_source_key, policy.evidence_article_no)
    raw = {"calculation_result": result.model_dump(mode="json"), "law_result": {"success": True, "primary_sources": [article], "references": []}}
    evidence, _ = EvidenceHub().collect(raw, {})
    report = CalculationRuleVerifier().verify(result, evidence)
    assert report.verdict == "PASS"


def test_product_alias_solomon_routes_as_product():
    hints = product_search_hints([{"product_name": "솔로몬 국공채 단기 증권자투자신탁(채권)", "source_file": "R2_demo.pdf"}])
    assert "솔로몬" in hints
    router = QueryRouter(hints)
    assert router.mentions_prospectus_product("솔로몬 국공채 단기·중장기·장기, 뭐가 달라요?")

def test_api_sync_policy_normalizer_fails_closed_and_accepts_required_values(tmp_path):
    from scripts.sync_legal_db import refresh_tax_credit_policy
    store = LegalStore(tmp_path / "legal.db")
    store.load_guardrail_registry()
    bad = {"effective_date": "20260701", "articles": [{"article_no": "제59조의3", "article_text": "불완전한 조문"}]}
    assert refresh_tax_credit_policy(store, bad) is False
    good = {"effective_date": "20260701", "articles": [{"article_no": "제59조의3", "article_text": "연금저축계좌 600만원, 합계 900만원, 100분의12, 총급여 5천500만원 이하이면 100분의15, 종합소득 4천500만원 이하, 추가 300만원, 100분의10"}]}
    assert refresh_tax_credit_policy(store, good) is True
    policy = TaxPolicyRepository(store).pension_tax_credit(2026)
    assert policy and policy.combined_credit_base_limit == Decimal("9000000")
