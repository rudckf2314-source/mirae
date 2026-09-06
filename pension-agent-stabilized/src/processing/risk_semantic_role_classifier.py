from __future__ import annotations

import re

from schemas.risk_extraction import RiskSemanticRole


SECTION_HEADINGS = {
    "위험",
    "투자위험",
    "주요투자위험",
    "집합투자기구의투자위험",
    "투자위험의주요내용",
}
RISK_CATEGORIES = {"일반위험", "특수위험", "기타투자위험"}
RISK_CATEGORY_RE = re.compile(r"^(?:[가-라]\d*)?(?:일반위험|특수위험|기타투자위험|기타위험)(?:등)?$")
NUMBERED_SECTION_RE = re.compile(r"^\d+(?:의\d+)?(?:집합투자기구의)?투자위험")


def compact_semantic_text(text: str | None) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text or "")


class RiskSemanticRoleClassifier:
    def classify(self, text: str | None, *, is_header: bool = False) -> RiskSemanticRole:
        compact = compact_semantic_text(text)
        if not compact:
            return RiskSemanticRole.OTHER
        if is_header:
            header_role = self._classify_header(compact)
            if header_role != RiskSemanticRole.TABLE_HEADER:
                return header_role
            return RiskSemanticRole.TABLE_HEADER
        if compact in SECTION_HEADINGS or NUMBERED_SECTION_RE.match(compact):
            return RiskSemanticRole.SECTION_HEADING
        if compact in RISK_CATEGORIES or RISK_CATEGORY_RE.match(compact):
            return RiskSemanticRole.RISK_CATEGORY
        if "원인" in compact or "발생요인" in compact:
            return RiskSemanticRole.RISK_CAUSE
        if any(token in compact for token in ("영향", "결과", "손실내용")):
            return RiskSemanticRole.RISK_IMPACT
        if any(token in compact for token in ("완화", "대응", "관리방안", "위험관리")):
            return RiskSemanticRole.RISK_MITIGATION
        if (
            ("투자위험" in compact or compact.startswith("위험"))
            and any(token in compact for token in ("주요내용", "설명", "내용"))
        ):
            return RiskSemanticRole.RISK_DESCRIPTION
        if len(compact) <= 40 and "위험" in compact:
            return RiskSemanticRole.RISK_NAME
        if (
            len(compact) <= 40
            and compact.endswith("가능성")
            and any(token in compact for token in ("괴리", "손실", "부도", "미상환"))
        ):
            return RiskSemanticRole.RISK_NAME
        return RiskSemanticRole.OTHER

    def _classify_header(self, compact: str) -> RiskSemanticRole:
        if self._is_name_header(compact):
            return RiskSemanticRole.RISK_NAME
        if "원인" in compact or "발생요인" in compact:
            return RiskSemanticRole.RISK_CAUSE
        if any(token in compact for token in ("영향", "결과", "손실내용")):
            return RiskSemanticRole.RISK_IMPACT
        if any(token in compact for token in ("완화", "대응", "관리방안", "위험관리")):
            return RiskSemanticRole.RISK_MITIGATION
        if (
            ("투자위험" in compact or compact.startswith("위험"))
            and any(token in compact for token in ("주요내용", "설명", "내용"))
        ):
            return RiskSemanticRole.RISK_DESCRIPTION
        return RiskSemanticRole.TABLE_HEADER

    @staticmethod
    def _is_name_header(compact: str) -> bool:
        return compact in {
            "구분",
            "세부구분",
            "위험구분",
            "투자위험구분",
            "위험명",
            "투자위험명",
            "항목",
        }
