import re

from schemas.chunk import SectionSpan, SectionType
from schemas.document import ParsedDocument

SECTION_KEYWORDS: dict[SectionType, list[str]] = {
    SectionType.PRODUCT_INFO: [
        "집합투자기구 명칭",
        "집합투자업자 명칭",
        "작성 기준일",
        "작성기준일",
        "효력발생일",
        "펀드코드",
        "투 자 설 명 서",
        "투자설명서",
    ],
    SectionType.RISK_GRADE: [
        "투자위험등급",
        "위험등급",
        "매우 높은 위험",
        "낮은 위험",
        "보통 위험",
    ],
    SectionType.INVESTMENT_OBJECTIVE: [
        "투자목적",
        "집합투자기구의 투자목적",
    ],
    SectionType.INVESTMENT_STRATEGY: [
        "투자전략",
        "위험관리",
        "수익구조",
    ],
    SectionType.INVESTMENT_RISK: [
        "투자위험",
        "원본손실위험",
        "주요 투자위험",
        "투자위험 및",
    ],
    SectionType.CLASS_INFO: [
        "클래스 종류",
        "클래스종류",
        "종류형",
        "종류 집합투자증권",
        "수수료선취",
        "수수료미징구",
    ],
    SectionType.FEES: [
        "총보수",
        "판매수수료",
        "판매보수",
        "보수 및 수수료",
        "환매수수료",
        "투자자가 부담하는 수수료",
        "투자비용",
    ],
    SectionType.PERFORMANCE: [
        "투자실적",
        "연평균수익률",
        "연평균 수익률",
        "운용실적",
        "수익률 변동성",
        "비교지수",
        "설정일이후",
        "최근 1년",
    ],
    SectionType.AUM: [
        "순자산총액",
        "순자산",
        "설정액",
        "운용규모",
        "자산총액",
    ],
}

INTRA_PAGE_HEADINGS: list[tuple[re.Pattern[str], SectionType]] = [
    (re.compile(r"투자목적\s*및\s*투자전략"), SectionType.INVESTMENT_STRATEGY),
    (re.compile(r"집합투자기구의\s*투자목적"), SectionType.INVESTMENT_OBJECTIVE),
    (re.compile(r"투자목적"), SectionType.INVESTMENT_OBJECTIVE),
    (re.compile(r"투자전략"), SectionType.INVESTMENT_STRATEGY),
    (re.compile(r"주요\s*투자위험"), SectionType.INVESTMENT_RISK),
    (re.compile(r"투자비용"), SectionType.FEES),
    (re.compile(r"투자실적"), SectionType.PERFORMANCE),
    (re.compile(r"(?:^|\n)\s*분류(?:\s|\n|$)"), SectionType.PRODUCT_INFO),
    (re.compile(r"투자위험등급|위험등급"), SectionType.RISK_GRADE),
    (re.compile(r"운용규모\s*[:：]|순자산총액"), SectionType.AUM),
]


class SectionDetector:
    def detect(self, parsed: ParsedDocument) -> list[SectionSpan]:
        spans: list[SectionSpan] = []
        for page in parsed.pages:
            intra = self._split_page(page.text, page.page_number)
            if intra:
                spans.extend(intra)
                continue
            section_type, score, hits = self._classify_page(page.text, page.page_number)
            spans.append(
                SectionSpan(
                    section_type=section_type,
                    page_start=page.page_number,
                    page_end=page.page_number,
                    score=score,
                    keywords_hit=sorted(set(hits)),
                    text=page.text,
                )
            )
        return spans

    def classify_text(self, text: str) -> tuple[SectionType, float, list[str]]:
        return self._classify_page(text, page_number=0)

    def _split_page(self, text: str, page_number: int) -> list[SectionSpan]:
        matches: list[tuple[int, SectionType, str]] = []
        for pattern, section_type in INTRA_PAGE_HEADINGS:
            for found in pattern.finditer(text):
                matches.append((found.start(), section_type, found.group(0)))
        if len(matches) < 2:
            return []
        matches.sort(key=lambda item: item[0])
        deduped: list[tuple[int, SectionType, str]] = []
        for start, section_type, heading in matches:
            if deduped and start - deduped[-1][0] < 8:
                continue
            deduped.append((start, section_type, heading))
        if len(deduped) < 2:
            return []

        spans: list[SectionSpan] = []
        if deduped[0][0] > 0:
            prefix = text[: deduped[0][0]].strip()
            if prefix:
                prefix_type, score, hits = self._classify_page(prefix, page_number)
                spans.append(
                    SectionSpan(
                        section_type=prefix_type,
                        page_start=page_number,
                        page_end=page_number,
                        heading=None,
                        score=score,
                        keywords_hit=hits,
                        text=prefix,
                    )
                )
        for index, (start, section_type, heading) in enumerate(deduped):
            end = deduped[index + 1][0] if index + 1 < len(deduped) else len(text)
            block = text[start:end].strip()
            if not block:
                continue
            spans.append(
                SectionSpan(
                    section_type=section_type,
                    page_start=page_number,
                    page_end=page_number,
                    heading=heading,
                    score=2.0,
                    keywords_hit=[heading],
                    text=block,
                )
            )
        return spans

    def _classify_page(self, text: str, page_number: int) -> tuple[SectionType, float, list[str]]:
        compact = re.sub(r"\s+", "", text)
        scores: dict[SectionType, float] = {}
        hits: dict[SectionType, list[str]] = {}

        for section_type, keywords in SECTION_KEYWORDS.items():
            matched = [kw for kw in keywords if kw.replace(" ", "") in compact or kw in text]
            if matched:
                scores[section_type] = float(len(matched))
                hits[section_type] = matched

        if page_number <= 2:
            scores[SectionType.PRODUCT_INFO] = scores.get(SectionType.PRODUCT_INFO, 0) + 1.5
            scores[SectionType.RISK_GRADE] = scores.get(SectionType.RISK_GRADE, 0) + 1.0

        if not scores:
            return SectionType.OTHER, 0.0, []

        best_type = max(scores, key=lambda key: scores[key])
        return best_type, scores[best_type], hits.get(best_type, [])
