import json
from pathlib import Path

from chatbot.adaptive_query import AdaptiveQueryAnalyzer
from chatbot.evidence_coverage import EvidenceCoverageChecker
from chatbot.rag_metrics import evaluate_rankings
from chatbot.retriever import ChunkRetriever, query_variants


def _chunk(i: int, text: str) -> dict:
    return {
        "chunk_id": f"c{i}", "document_id": f"d{i}", "source_group": "docs",
        "source_label": "문서", "filename": f"f{i}.pdf", "location_type": "page",
        "location": i, "text": text,
    }


def test_query_refinement_decomposes_comparison_and_tax():
    result = AdaptiveQueryAnalyzer().analyze("DC와 IRP를 비교하고 세액공제 한도도 알려줘")
    assert result.needs_decomposition
    assert len(result.retrieval_queries) >= 2
    assert result.required_evidence


def test_retriever_uses_multi_query_rrf_bm25_tfidf(tmp_path: Path):
    path = tmp_path / "chunks.jsonl"
    rows = [
        _chunk(1, "확정기여형 DC는 사용자가 적립금을 운용하며 운용 결과에 따라 급여가 달라질 수 있습니다."),
        _chunk(2, "개인형퇴직연금 IRP는 퇴직급여를 이전하여 운용할 수 있는 계좌입니다."),
        _chunk(3, "단순 행사 안내와 질문 리스트입니다."),
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    results = ChunkRetriever(path).retrieve("DC 운용 주체가 누구야?", top_k=2, source_group="docs")
    assert results
    assert results[0]["chunk_id"] == "c1"
    assert results[0]["retrieval_method"] == "multi_query_rrf_bm25_tfidf"
    assert results[0]["query_variant_count"] >= 2


def test_query_variants_include_tax_refinement():
    variants = query_variants("IRP 세액공제는 얼마야?")
    assert any("공제 대상" in v for v in variants)


def test_fact_coverage_is_diagnostic_not_hard_gate():
    report = EvidenceCoverageChecker().check(
        ["document"], [{"domain": "document", "text": "DC는 근로자가 운용합니다."}],
        required_facts=["DC 운용", "세액공제 한도"],
    )
    assert report.complete is True
    assert report.fact_coverage_score < 1.0
    assert "세액공제 한도" in report.uncovered_facts


def test_retrieval_metric_math():
    m = evaluate_rankings([["a", "x", "b"], ["z", "c"]], [{"a", "b"}, {"c"}], k=3)
    assert m.recall_at_k == 1.0
    assert m.mrr_at_k > 0.7
    assert 0 < m.ndcg_at_k <= 1
