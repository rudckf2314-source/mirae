from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chatbot.irp_eligibility import evaluate_irp_eligibility
from chatbot.pension_protocol import ResponseGuard
from chatbot.public_security import public_payload
from chatbot.pension_specs import VerificationSpec
from chatbot.pension_evidence import EvidenceHub
from chatbot.pension_verifier import RuleVerifier
from chatbot.claim_grounding import ClaimGroundingVerifier
from chatbot.pension_langgraph_agent import PensionLangGraphAgent
from chatbot.agent_core import PensionAgentCore
from chatbot.query_router import RouteDecision
from chatbot.source_contract import canonical_route, source_usage
from chatbot.legal_store import LegalStore, LegalArticle


@pytest.mark.parametrize('text', ['IRP 가입 불가', 'IRP 가입 불가능', 'IRP 가입 대상이 아님', '개인형퇴직연금 가입 금지'])
def test_negative_irp_is_never_eligible(text):
    assert evaluate_irp_eligibility({'eligibility_text': text})['status'] == 'INELIGIBLE'


@pytest.mark.parametrize('label', ['C-P2', 'C-P2e', 'CP2'])
def test_share_class_requires_additional_irp_evidence(label):
    assert evaluate_irp_eligibility({'class_name': label})['status'] == 'UNRESOLVED'


def test_irp_affirmation_and_conflict():
    assert evaluate_irp_eligibility({'eligibility_text': 'IRP 가입 가능'})['status'] == 'ELIGIBLE'
    assert evaluate_irp_eligibility({'pension_type_codes': ['IRP'], 'eligibility_text': 'IRP 가입 불가'})['status'] == 'UNRESOLVED'


def test_product_row_does_not_waive_provenance():
    raw = {'tools': ['product'], 'product_results': [{'product_name': 'fixture', 'record_id': 'one'}]}
    evidence, _ = EvidenceHub().collect(raw, {})
    report = RuleVerifier().verify(VerificationSpec(require_pdf_evidence=True), None, raw, evidence, {})
    assert report.verdict == 'FAIL'


def test_document_does_not_replace_required_law():
    raw = {'tools': ['document', 'law'], 'results': [{'filename': 'fixture.pdf', 'location': 1, 'text': '일반 안내'}]}
    evidence, _ = EvidenceHub().collect(raw, {'pdf_index': 'fixture'})
    report = RuleVerifier().verify(VerificationSpec(require_law_evidence=True), None, raw, evidence, {})
    assert report.verdict == 'FAIL'


def test_document_metadata_without_text_never_passes():
    raw = {'tools': ['document'], 'results': [{'filename': 'fixture.pdf', 'location': 1, 'text': ''}]}
    evidence, _ = EvidenceHub().collect(raw, {'pdf_index': 'fixture'})
    report = RuleVerifier().verify(VerificationSpec(), None, raw, evidence, {'pdf_index': 'fixture'})
    assert report.verdict == 'FAIL'
    assert 'evidence_origin_text' in report.failures


def _result(verdict='PASS', route='document'):
    return {'route': route, 'answer': '한도는 50%입니다.', 'results': [{'filename': 'fixture.pdf', 'location': 1, 'text': '한도는 50%입니다.'}],
            'langgraph': {'verification_verdict': verdict}}


@pytest.mark.parametrize('verdict', ['FAIL', 'AMBIGUOUS', None])
def test_final_guard_blocks_nonpass(verdict):
    response = ResponseGuard().guard(_result(verdict), 'test')
    assert response['status'] != 'success'
    assert '50%' not in response['answer']


def test_guard_blocks_both_without_product_evidence():
    response = ResponseGuard().guard(_result(route='both'), 'test')
    assert response['status'] == 'safe_stop'
    assert response['metadata']['route'] == 'document+product'


def test_guard_blocks_both_without_document_evidence():
    result = _result(route='both')
    result['results'] = []
    result['product_results'] = [{'record_id': 'one', 'source_file': 'fixture.pdf'}]
    assert ResponseGuard().guard(result, 'test')['status'] == 'safe_stop'


def test_bearer_and_nested_paths_redacted():
    value = {'answer': 'Authorization: Bearer abcdef1234567890abcdef', 'sources': [{'source_file': r'C:\private\test.pdf'}],
             'metadata': {'note': '/workspace/private/file.env', 'raw_answer': 'private'}}
    public = json.dumps(public_payload(value))
    assert 'abcdef1234567890abcdef' not in public
    assert 'private' not in public


def test_numeric_paragraph_not_removed():
    answer = '다음은 적용 조건과 금액을 설명하는 안내입니다.\n\n09:20 / 50% / 2026-07-01'
    assert '09:20' in ResponseGuard._sanitize_answer(answer)


@pytest.mark.parametrize('answer,evidence,verdict', [
    ('50%', '150%', 'REVIEW'), ('900만원', '9,000,000원', 'PASS'),
    ('50%', '50원', 'REVIEW'), ('50%', '100분의 50', 'PASS'),
    ('09:20', '09:30', 'REVIEW'), ('0.5%', '50%', 'REVIEW')])
def test_numeric_units_and_boundaries(answer, evidence, verdict):
    assert ClaimGroundingVerifier().verify(answer, [evidence]).verdict == verdict


def test_question_cannot_ground_its_own_number():
    fake = SimpleNamespace(claim_grounding_verifier=ClaimGroundingVerifier())
    state = {'route': 'document', 'worker_results': {'document': {'question': '777%인가요?', 'answer': '777%입니다.'}},
             'evidence': [], 'verification_report': {'verdict': 'PASS'}}
    result = PensionLangGraphAgent._claim_grounding_node(fake, state)
    assert result['safe_stop_reason'] == 'unsupported_numeric_claims'
    assert result['verification_report']['verdict'] == 'FAIL'
    assert '777%' not in result['worker_results']['document']['answer']


def test_actual_backend_not_inferred_from_route():
    assert source_usage({'route': 'product'})['product_lookup_used'] is False
    usage = source_usage({'route': 'both', 'product_lookup_used': True, 'product_backend': 'standard_json', 'product_results': []})
    assert usage['backend'] == 'standard_json'
    assert usage['product_records_used'] is False


def test_both_collection_never_calls_law():
    core = PensionAgentCore.__new__(PensionAgentCore)
    core.product_db = SimpleNamespace(available=False, backend='standard_json')
    core.document_chatbot = SimpleNamespace(llm=SimpleNamespace(model='fixture'), retriever=SimpleNamespace(retrieve=lambda *a, **k: [{'filename': 'fixture.pdf', 'location': 1, 'text': '안내'}]))
    core._search_law_result = Mock(side_effect=AssertionError('unexpected law call'))
    collection = core._collect_evidence_base('상품과 문서 비교', RouteDecision(['document', 'product'], 'fixture'))
    assert canonical_route(collection['result']['route']) == 'document+product'
    assert collection['result']['results']
    core._search_law_result.assert_not_called()


def test_both_worker_accepts_both_route():
    delegate = Mock(return_value={'ok': True})
    fake = SimpleNamespace(_delegate_legacy_route=delegate)
    state = {'route': 'both'}
    PensionLangGraphAgent._product_worker(fake, state)
    delegate.assert_called_once_with(state, 'both')


def _article(effective, text):
    return LegalArticle('RETIREMENT_BENEFIT_ACT', 'fixture', 'law', None, None, None, effective, '제24조', 'fixture', text, 'fixture', None, '2026-09-06')


def test_legal_asof_and_duplicate_versions(tmp_path):
    store = LegalStore(tmp_path / 'law.db'); store.load_guardrail_registry()
    store.upsert_articles([_article('20250101', 'old'), _article('20260101', 'current'), _article('20270101', 'future')])
    assert store.get_article('RETIREMENT_BENEFIT_ACT', '제24조', as_of='2026-09-06')['article_text'] == 'current'
    rows = store.get_articles_for_sources(['RETIREMENT_BENEFIT_ACT'], as_of='20260906')
    assert len(rows) == 1 and rows[0]['article_text'] == 'current'
    assert store.get_articles_for_sources(['RETIREMENT_BENEFIT_ACT'], allowed_articles={'RETIREMENT_BENEFIT_ACT': set()}, as_of='20260906') == []
    store.upsert_articles([_article('20260101', 'conflicting')])
    assert store.get_article('RETIREMENT_BENEFIT_ACT', '제24조', as_of='20260906') is None


def test_submission_http_contract(monkeypatch):
    from fastapi.testclient import TestClient
    import chatbot.web as web
    seen = []
    def respond(question, **kwargs):
        seen.append((question, kwargs))
        return ResponseGuard().guard(_result(), kwargs['question_id'])
    monkeypatch.setattr(web, 'langgraph_agent', SimpleNamespace(respond=respond))
    with TestClient(web.app) as client:
        response = client.get('/answer', params={'question': '질문', 'question_id': 'case-1', 'top_k': 3})
        assert response.status_code == 200
        payload = response.json()
        assert {'question', 'question_id', 'retrieved_context', 'think_trace', 'answer'} <= payload.keys()
        assert payload['question_id'] == 'case-1'
        assert payload['retrieved_context'][0]['excerpt']
        assert isinstance(payload['think_trace'], str)
        assert seen[0][1]['top_k'] == 3
        assert client.post('/api/search', json={'question': '질문'}).status_code == 200


def test_public_http_error_hides_private_exception(monkeypatch):
    from fastapi.testclient import TestClient
    import chatbot.web as web
    def fail(*args, **kwargs):
        raise RuntimeError('Authorization: Bearer privateSecret /workspace/private')
    monkeypatch.setattr(web, 'langgraph_agent', SimpleNamespace(respond=fail))
    with TestClient(web.app) as client:
        response = client.get('/answer', params={'question': '질문', 'question_id': 'error-1'})
        assert response.status_code == 500
        assert 'private' not in response.text
        assert response.json()['detail']['question_id'] == 'error-1'


@pytest.mark.parametrize('answer,token,present', [
    ('150%', '50', False), ('50%', '50', True),
    ('50원', '50%', False), ('900만원', '9000000원', True),
    ('09:30', '09:20', False),
])
def test_gold_v2_numeric_matching(answer, token, present):
    from tests.gold100.gold100_evaluator import number_present
    assert number_present(answer, token) is present


def test_gold_citation_word_without_source_is_not_evidence():
    from tests.gold100.gold100_evaluator import citation_coverage
    assert not citation_coverage('법령 근거에 따르면 50%입니다.', [], {})['covered']


def test_trace_reports_actual_json_backend_and_no_external_law_call():
    from tests.agent_eval.run_eval import collect_trace
    envelope = {'status': 'success', 'sources': [{'domain': 'product'}], 'metadata': {
        'route': 'both', 'product_lookup_used': True, 'backend': 'standard_json',
        'product_records_used': True, 'document_lookup_used': True, 'external_api_used': False}}
    trace = collect_trace(question='질문', envelope=envelope, state=None, adapter=None)
    assert trace['actual_route'] == 'document+product'
    assert trace['structured_product_used'] is True
    assert trace['postgres_used'] is False
    assert trace['external_api_used'] is False
    assert 'postgres' not in trace['source_types']


def test_route_name_alone_does_not_prove_retrieval():
    from tests.agent_eval.run_eval import collect_trace
    trace = collect_trace(question='질문', envelope={'metadata': {'route': 'product+law'}}, state=None, adapter=None)
    assert trace['tools_called'] == []
    assert not trace['external_api_used']
    assert not trace['structured_product_used']


def test_law_api_session_closed_and_count_is_request_local(monkeypatch):
    from chatbot.law_tool import LawTool
    session = Mock()
    api = SimpleNamespace(request_count=1, session=session)
    tool = LawTool.__new__(LawTool)
    tool.retriever = Mock()
    tool._api_client = None
    tool.allow_api_fallback = True
    def retrieve(local, question):
        local._api_client = api
        return {'success': True, 'primary_sources': []}
    monkeypatch.setattr(LawTool, '_search_impl', retrieve)
    result = tool.search('fixture')
    assert result['external_api_used'] is True
    session.close.assert_called_once()
    assert tool._api_client is None


def test_law_db_only_has_no_external_call(monkeypatch):
    from chatbot.law_tool import LawTool
    tool = LawTool.__new__(LawTool)
    tool.retriever = Mock()
    tool._api_client = None
    tool.allow_api_fallback = False
    monkeypatch.setattr(LawTool, '_search_impl', lambda self, question: {'success': True})
    assert tool.search('fixture')['external_api_used'] is False


def test_bundled_gold_cases_match_historical_questions():
    root = Path(__file__).resolve().parents[1]
    baseline = root / 'reports/gold100_to_92/gold100_hotfix/gold100_cases.json'
    bundled = root / 'tests/gold100/fixtures/cases.json'
    assert baseline.read_bytes() == bundled.read_bytes()


def test_law_cache_hit_is_not_a_live_api_call():
    from chatbot.pension_cache import ToolCacheContext
    cached = {'success': True, 'external_api_used': True}
    controller = SimpleNamespace(lookup=Mock(return_value=(cached, 'hit', None)),
                                 source_versions=SimpleNamespace(law_policy='fixture'))
    loader = Mock(side_effect=AssertionError('cache must not reload'))
    result = ToolCacheContext(controller).law_result('fixture', loader)
    assert result['external_api_used'] is False
    assert result['retrieval_cache'] == 'hit'
    assert cached['external_api_used'] is True
    loader.assert_not_called()


def test_law_db_change_invalidates_source_version(tmp_path):
    from chatbot.pension_cache import SourceVersionTracker
    db = tmp_path / 'law.db'
    db.write_bytes(b'first')
    tracker = SourceVersionTracker(lambda: [], lambda: [], lambda: [db])
    before = tracker.versions.law_policy
    db.write_bytes(b'changed database')
    tracker.refresh()
    assert tracker.versions.law_policy != before
