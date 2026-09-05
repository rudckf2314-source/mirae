from __future__ import annotations

import re

from parsers.table_parser import is_semantic_risk_table
from processing.risk_semantic_role_classifier import RiskSemanticRoleClassifier
from schemas.chunk import Chunk, SectionType
from schemas.document import ParsedDocument
from schemas.risk_extraction import RiskRegion, RiskSemanticRole, RiskSourceSpan


RISK_REGION_MARKERS = (
    "집합투자기구의투자위험",
    "주요투자위험",
    "일반위험",
    "특수위험",
    "기타투자위험",
    "위험요인",
)
STOP_MARKERS = ("매입방법", "환매방법", "판매수수료", "기준가격", "보수및수수료")


def normalize_region_text(text: str | None) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text or "")


class RiskRegionDetector:
    def __init__(self, classifier: RiskSemanticRoleClassifier | None = None):
        self.classifier = classifier or RiskSemanticRoleClassifier()

    def detect(
        self,
        parsed: ParsedDocument,
        chunks: list[Chunk] | None = None,
    ) -> list[RiskRegion]:
        chunks = chunks or []
        refs_by_page: dict[int, list[str]] = {}
        for chunk in chunks:
            if chunk.section_type == SectionType.INVESTMENT_RISK or chunk.table_id:
                refs_by_page.setdefault(chunk.page_start, []).append(chunk.chunk_id)

        tables_by_page: dict[int, list] = {}
        for table in parsed.tables:
            if self._is_risk_table(table):
                tables_by_page.setdefault(table.page_number, []).append(table)

        regions: list[RiskRegion] = []
        for page in parsed.pages:
            compact = normalize_region_text(page.text)
            tables = tables_by_page.get(page.page_number, [])
            has_named_marker = any(marker in compact for marker in RISK_REGION_MARKERS)
            has_semantic_pair = (
                "위험" in compact
                and any(token in compact for token in ("주요내용", "설명", "내용"))
            )
            risk_chunks = [
                chunk for chunk in chunks
                if chunk.page_start == page.page_number
                and chunk.section_type == SectionType.INVESTMENT_RISK
            ]
            if not (tables or has_named_marker or has_semantic_pair or risk_chunks):
                continue

            raw_blocks = [
                RiskSourceSpan(
                    source_id=block.block_id,
                    page_number=page.page_number,
                    raw_text=block.text,
                    normalized_text=re.sub(r"\s+", " ", block.text).strip(),
                    bbox=block.bbox,
                    evidence_refs=list(dict.fromkeys(refs_by_page.get(page.page_number, []))),
                )
                for block in page.blocks
                if not self._is_stop_heading(block.text)
            ]
            regions.append(
                RiskRegion(
                    region_id=f"{parsed.document_id}:risk:p{page.page_number:03d}",
                    document_id=parsed.document_id,
                    page_start=page.page_number,
                    page_end=page.page_number,
                    raw_text=page.text,
                    evidence_refs=list(dict.fromkeys(refs_by_page.get(page.page_number, []))),
                    table_ids=[table.table_id for table in tables],
                    raw_headers=[list(table.raw_headers or table.headers) for table in tables],
                    raw_rows=[[list(row) for row in table.rows] for table in tables],
                    raw_blocks=raw_blocks,
                )
            )
        return regions

    @staticmethod
    def _is_stop_heading(text: str) -> bool:
        # Region detection remains permissive; stop headings themselves are excluded.
        compact = normalize_region_text(text)
        return any(compact.startswith(marker) for marker in STOP_MARKERS)

    def _is_risk_table(self, table) -> bool:
        if is_semantic_risk_table(table):
            return True
        roles = {
            self.classifier.classify(header, is_header=True)
            for header in table.headers
        }
        return (
            RiskSemanticRole.RISK_NAME in roles
            and bool(roles & {
                RiskSemanticRole.RISK_DESCRIPTION,
                RiskSemanticRole.RISK_CAUSE,
                RiskSemanticRole.RISK_IMPACT,
                RiskSemanticRole.RISK_MITIGATION,
            })
        )
