from chatbot.answer_contract import (plan_slots, resolve_slots, enrich_collection,
    composition_instruction, requires_premise_check, is_tax_rule_question,
    extract_reject_code_matches, is_product_rule_question, is_transfer_reject_code_question)
from chatbot.product_entity_precision import is_generic_financial_noun
from chatbot.query_router import QueryRouter


def test_tax_options_are_not_an_unsupported_numeric_calculation():
    router = QueryRouter()
    for q in ["사적연금 과세 선택지가 소득 경계에서 달라지나요?", "공제받지 못한 납입금은 무효인가요? 세액공제와 구별해 주세요.", "퇴직금 세금을 나중으로 미루는 원리가 뭔가요?"]:
        assert is_tax_rule_question(q)
        assert router.decide(q).tools == ["document", "law"]
    assert not is_tax_rule_question("총급여 4000만원에 IRP 700만원 납입했을 때 세액공제액 계산")


def test_product_rule_is_not_a_catalog_listing():
    assert QueryRouter().decide("주식이 편입 당시 기준에서 벗어나면 매도하지 않아도 규정을 위반하나요?").tools == ["document"]
    assert "product" in QueryRouter().decide("위험등급 3등급 이하 상품을 보여줘").tools


def test_missing_fact_is_not_found_just_because_a_document_exists():
    slots = plan_slots("교체매매 주문은 언제 처리되며 기관 간 스위칭에는 며칠이 걸리나요?")
    resolved = resolve_slots(slots, [{"filename":"guide.pdf", "location":1, "text":"연금은 노후생활을 위한 제도입니다."}])
    assert all(s["status"] == "MISSING" for s in resolved)


def test_one_targeted_retrieval_retains_provenance_and_exact_time():
    class Retriever:
        calls = []
        def retrieve(self, question, **kwargs):
            self.calls.append(question)
            return [{"filename":"operations.pdf", "location":4, "chunk_id":"ops-4", "text":"교체매매 주문은 익일 오전 8시 15분 처리됩니다. 기관 간 스위칭 정산은 D+2영업일 소요됩니다."}]
    retriever = Retriever()
    collection = {"result":{"results":[]}, "answer_kind":"document", "contexts":[], "evidence_text":""}
    updated = enrich_collection("교체매매 처리 시점과 기관 간 스위칭은 며칠인가요?", collection, retriever, 5)
    contract = updated["required_facts"]
    assert len(retriever.calls) == contract["targeted_retrieval_count"] == 1
    assert contract["missing_count"] == 0
    assert "8시 15분" in str(contract)
    assert "D+2영업일" in str(contract)
    assert contract["slots"][0]["evidence"][0]["source_file"] == "operations.pdf"
    assert updated["result"]["results"][0]["chunk_id"] == "ops-4"


def test_conflicting_source_does_not_count_as_found():
    slots = resolve_slots(plan_slots("한도 비율은?"), [{"text":"한도는 20%입니다.", "status":"conflict"}])
    assert slots[0]["status"] == "CONFLICT"


def test_premise_check_never_forces_a_negative_answer():
    assert requires_premise_check("전부 가능한가요?")
    text = composition_instruction("이 규칙이 맞나요?", {})
    assert "무조건 부정하지" in text
    assert "판단 불가" in text


def test_generic_nouns_with_particles_are_not_proprietary_names():
    for name in ["자투자신탁에서", "자투자신탁이", "자투자신탁뿐만", "집합투자기구의", "모투자신탁으로", "모투자신탁에서", "증권모투자신탁(주식)의", "증권모투자신탁(주식)"]:
        assert is_generic_financial_noun(name)
    for name in ["가상초고수익증권자투자신탁", "미래에셋가상상품펀드", "가짜모투자신탁", "행복자투자신탁에서"]:
        assert not is_generic_financial_noun(name)


def test_invented_products_ignores_generic_stems_without_lowering_threshold():
    from tests.agent_eval.evaluators import invented_products
    catalog = {"흥국멀티크레딧증권자투자신탁[채권]", "미래에셋소비성장증권모투자신탁(주식)"}
    assert invented_products("모투자신탁에서 발생하는 운용보수", catalog) == []
    assert invented_products("미래에셋 소비성장 증권모투자신탁(주식)의 투자 전략", catalog) == []
    assert invented_products("존재하지않는초고수익증권자투자신탁의 보수", catalog) != []


def test_transfer_reject_code_maps_primary_row_not_catchall_99():
    question = "소규모 펀드 실물이전이 안 되면 불가 사유 코드와 사유를 알려주세요."
    assert is_transfer_reject_code_question(question)
    assert not is_product_rule_question(question)
    contexts = [
        {
            "filename": "doc34.xlsx",
            "location": "Sheet1",
            "chunk_id": "primary",
            "text": (
                "불가사유코드 | 불가사유\n"
                "01. 소규모 펀드 임의해지 | 가입자가 보유한 펀드가 소규모 펀드(잔고 50억 미만) 임의해지 대상 펀드에 해당 할 경우\n"
                "23.실물이전불가 | 디폴트옵션 상품\n"
            ),
        },
        {
            "filename": "doc34.xlsx",
            "location": "Sheet1",
            "chunk_id": "catchall",
            "text": (
                "99.기타 : 불가사유 | 01. 소규모 펀드 임의해지 ~ 25. 상품협약(위탁계약) 미체결 사유 이 외에 실물이전 불가 사유가 있을 경우\n"
                "[99. 기타 : 불가사유]로 표기\n"
            ),
        },
    ]
    matches = extract_reject_code_matches(question, contexts)
    assert matches and matches[0]["code"] == "01"
    assert matches[0]["reason"].startswith("소규모 펀드 임의해지")
    assert all(m["code"] != "99" for m in matches)
    slots = resolve_slots(plan_slots(question), contexts, question)
    code_slot = next(s for s in slots if s["slot"] == "reject_reason_code")
    assert code_slot["status"] == "FOUND"
    assert code_slot["evidence"][0]["mapped_code"] == "01"
    threshold = next(s for s in slots if s["slot"] == "small_fund_threshold")
    assert threshold["status"] == "FOUND"
    assert "50억" in threshold["evidence"][0]["quote"]
    prompt = composition_instruction(question, {
        "mapped_reject_codes": matches,
        "slots": slots,
    })
    assert "01. 소규모 펀드 임의해지" in prompt
    assert "99.기타는 01~25에 없는 기타 사유용" in prompt


def test_investment_limit_rule_is_not_catalog_search():
    from chatbot.answer_contract import is_investment_limit_rule_question
    q = "표준 채권형 자투자신탁의 장외파생 위험평가액 한도와 사모펀드 편입 한도를 대조해 주세요."
    assert is_investment_limit_rule_question(q)
    tools = QueryRouter().decide(q).tools
    assert "document" in tools
    assert "product" in tools


def test_named_nickname_benchmark_routes_to_product():
    q = "미래에셋아세안셀렉트Q 자펀드가 아시아 전체이고 MSCI World가 벤치마크라면 맞나요?"
    assert "product" in QueryRouter().decide(q).tools
