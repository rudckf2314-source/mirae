from chatbot.adaptive_query import AdaptiveQueryAnalyzer
from chatbot.evidence_coverage import EvidenceCoverageChecker
from chatbot.claim_grounding import ClaimGroundingVerifier
from chatbot.domain_registry import DomainRegistry


def test_adaptive_query_detects_composite_pension_question():
    result = AdaptiveQueryAnalyzer().analyze(
        "DC와 IRP의 운용 제한을 비교하고 세액공제도 같이 알려줘"
    )
    assert result.complexity in {"medium", "high"}
    assert "DC" in result.entities["accounts"]
    assert "IRP" in result.entities["accounts"]
    assert "comparison" in result.intents
    assert "tax" in result.intents


def test_recommendation_extracts_missing_profile_fields():
    result = AdaptiveQueryAnalyzer().analyze("좋은 연금 상품 하나 추천해 주세요")
    assert "conditional_recommendation" in result.intents
    assert "위험 감내 수준" in result.missing_information
    assert "투자 기간" in result.missing_information


def test_evidence_coverage_requires_every_planned_domain():
    checker = EvidenceCoverageChecker()
    report = checker.check(
        ["product", "law"],
        [{"domain": "product", "text": "상품 근거"}],
        {"product_results": [{"product_name": "A"}]},
    )
    assert report.complete is False
    assert report.score == 0.5
    assert report.missing_domains == ("law",)


def test_claim_grounding_flags_new_numbers():
    verifier = ClaimGroundingVerifier()
    report = verifier.verify(
        "세액공제 한도는 900만원입니다.",
        ["제공 근거에는 600만원까지만 적혀 있습니다."],
    )
    assert report.verdict == "REVIEW"
    assert report.unsupported_numeric_claims


def test_domain_registry_is_extensible_without_graph_if_chain():
    registry = DomainRegistry()
    assert set(["document", "product", "law", "calculation"]).issubset(registry.names())
    assert registry.get("law").knowledge_mode == "rule_temporal"
