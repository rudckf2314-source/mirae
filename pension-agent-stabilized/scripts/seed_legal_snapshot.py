from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chatbot.legal_store import LegalArticle, LegalStore, utc_now  # noqa: E402

# Seed only the articles independently verified from current official law.go.kr pages.
# Periodic Open API sync replaces/extends this snapshot without requiring chat-time HTTP.
SEED = [
    {
        "source_key": "INCOME_TAX_ACT",
        "law_name": "소득세법",
        "law_type": "ACT",
        "effective_date": "20260701",
        "promulgation_date": "20251223",
        "article_no": "제59조의3",
        "article_title": "연금계좌세액공제",
        "source_url": "https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1032884269",
        "article_text": """제59조의3(연금계좌세액공제) ① 종합소득이 있는 거주자가 연금계좌에 납입한 금액 중 법정 제외금액을 뺀 연금계좌 납입액의 100분의 12[종합소득금액 4천500만원 이하(근로소득만 있는 경우 총급여액 5천500만원 이하)인 거주자는 100분의 15]를 종합소득산출세액에서 공제한다. 연금저축계좌 납입액은 연 600만원까지만 인정하고, 그 600만원 이내 금액과 퇴직연금계좌 납입액의 합계는 연 900만원까지만 인정한다. ③ ISA 계약기간 만료 후 잔액의 전부 또는 일부를 대통령령이 정하는 방법으로 연금계좌에 납입한 전환금액은 해당 과세기간 연금계좌 납입액에 포함한다. ④ 전환금액이 있는 경우 전환금액의 10% 또는 300만원 중 적은 금액을 추가 한도로 반영한다.""",
    },
    {
        "source_key": "INCOME_TAX_DECREE",
        "law_name": "소득세법 시행령",
        "law_type": "ENFORCEMENT_DECREE",
        "effective_date": "20260701",
        "promulgation_date": "20260522",
        "article_no": "제40조의2",
        "article_title": "연금계좌 등",
        "source_url": "https://www.law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1032589347",
        "article_text": """제40조의2(연금계좌 등) ② 연금계좌 가입자가 법정 요건을 갖춘 경우 법 제59조의3제1항의 연금계좌 납입액으로 본다. 해당 과세기간 이전의 연금보험료는 원칙적으로 납입할 수 없으며, 보험계약의 경우에는 법령이 정한 예외 기간이 적용될 수 있다.""",
    },
    {
        "source_key": "RETIREMENT_BENEFIT_ACT",
        "law_name": "근로자퇴직급여 보장법",
        "law_type": "ACT",
        "effective_date": "20260701",
        "promulgation_date": "20260317",
        "article_no": "제13조",
        "article_title": "확정급여형퇴직연금제도의 설정",
        "source_url": "https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1033846215",
        "article_text": """제13조(확정급여형퇴직연금제도의 설정) 확정급여형퇴직연금제도를 설정하려는 사용자는 근로자대표의 동의를 얻거나 의견을 들어 급여수준, 급여 지급능력 확보, 급여의 종류 및 수급요건, 운용관리업무 및 자산관리업무 등 법정 사항을 포함한 퇴직연금규약을 작성하여 고용노동부장관에게 신고하여야 한다.""",
    },
    {
        "source_key": "RETIREMENT_BENEFIT_ACT",
        "law_name": "근로자퇴직급여 보장법",
        "law_type": "ACT",
        "effective_date": "20260701",
        "promulgation_date": "20260317",
        "article_no": "제19조",
        "article_title": "확정기여형퇴직연금제도의 설정",
        "source_url": "https://www.law.go.kr/lsInfoP.do?ancYnChk=0&lsId=009883",
        "article_text": """제19조(확정기여형퇴직연금제도의 설정) 확정기여형퇴직연금제도를 설정하려는 사용자는 부담금의 부담·산정·납입, 적립금 운용, 운용방법과 정보 제공, 사전지정운용제도, 적립금 중도인출 등 법정 사항을 포함한 확정기여형퇴직연금규약을 작성하여 신고하여야 한다.""",
    },
    {
        "source_key": "RETIREMENT_BENEFIT_ACT",
        "law_name": "근로자퇴직급여 보장법",
        "law_type": "ACT",
        "effective_date": "20260701",
        "promulgation_date": "20260317",
        "article_no": "제22조",
        "article_title": "적립금의 중도인출",
        "source_url": "https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1016105385",
        "article_text": """제22조(적립금의 중도인출) 확정기여형퇴직연금제도에 가입한 근로자는 주택구입 등 대통령령으로 정하는 사유가 발생하면 적립금을 중도인출할 수 있다.""",
    },
    {
        "source_key": "RETIREMENT_BENEFIT_ACT",
        "law_name": "근로자퇴직급여 보장법",
        "law_type": "ACT",
        "effective_date": "20260701",
        "promulgation_date": "20260317",
        "article_no": "제24조",
        "article_title": "개인형퇴직연금제도의 설정 및 운영 등",
        "source_url": "https://www.law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=900197192",
        "article_text": """제24조(개인형퇴직연금제도의 설정 및 운영 등) ① 퇴직연금사업자는 개인형퇴직연금제도를 운영할 수 있다. ② 퇴직급여제도의 일시금을 수령한 사람, 퇴직연금제도 가입자가 자기 부담으로 추가 설정하려는 경우, 자영업자 등 법령이 정한 사람은 개인형퇴직연금제도를 설정할 수 있다. ③ 가입자는 자기 부담으로 부담금을 납입하되 대통령령이 정한 한도를 초과할 수 없다. ⑤ 급여의 종류별 수급요건 및 중도인출은 대통령령으로 정한다.""",
    },
    {
        "source_key": "RETIREMENT_BENEFIT_DECREE",
        "law_name": "근로자퇴직급여 보장법 시행령",
        "law_type": "ENFORCEMENT_DECREE",
        "effective_date": "20260324",
        "promulgation_date": "20260324",
        "article_no": "제18조",
        "article_title": "개인형퇴직연금제도의 급여 종류별 수급요건 및 중도인출",
        "source_url": "https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1032589237",
        "article_text": """제18조(개인형퇴직연금제도의 급여 종류별 수급요건 및 중도인출) ① 개인형퇴직연금의 연금은 55세 이상 가입자에게 지급하며 연금 지급기간은 5년 이상이어야 한다. 일시금은 55세 이상으로서 일시금 수급을 원하는 가입자에게 지급한다. ② 가입자가 법령이 정한 사유에 해당하면 개인형퇴직연금 적립금을 중도인출할 수 있다.""",
    },
]


def main() -> None:
    store = LegalStore()
    store.load_guardrail_registry()
    now = utc_now()
    records = [LegalArticle(
        source_key=item["source_key"], law_name=item["law_name"], law_type=item["law_type"],
        law_id=None, law_serial=None, promulgation_date=item.get("promulgation_date"),
        effective_date=item.get("effective_date"), article_no=item["article_no"],
        article_title=item.get("article_title"), article_text=item["article_text"],
        source_channel="LAW_GO_KR_OFFICIAL_SNAPSHOT", source_url=item.get("source_url"), fetched_at=now,
    ) for item in SEED]
    store.upsert_articles(records)

    # Current 2026 policy normalization.  Numeric policy is deterministic and
    # points back to the stored authoritative article instead of to an LLM.
    store.upsert_policy_rule(
        policy_key="PENSION_TAX_CREDIT", policy_year=2026, effective_from="2026-07-01",
        formula_id="pension_tax_credit_v2026", version="2026-07-01",
        payload={
            "combined_credit_base_limit": 9000000,
            "pension_savings_credit_base_limit": 6000000,
            "annual_contribution_limit": 18000000,
            "standard_rate": "0.12",
            "lower_income_rate": "0.15",
            "local_tax_surcharge_ratio": "0.10",
            "gross_salary_threshold": 55000000,
            "comprehensive_income_threshold": 45000000,
            "isa_extra_credit_base_limit": 3000000,
            "isa_transfer_credit_ratio": "0.10",
        },
        evidence_source_key="INCOME_TAX_ACT", evidence_article_no="제59조의3",
        source_type="LAW_GO_KR_OFFICIAL_SNAPSHOT", source_priority="OFFICIAL_LEGAL", verified=True,
    )
    print(store.stats())


if __name__ == "__main__":
    main()
