from __future__ import annotations

import re

from processing.risk_semantic_role_classifier import RiskSemanticRoleClassifier
from schemas.document import DetectedTable
from schemas.risk_extraction import (
    RiskRegion,
    RiskSemanticRole,
    RiskStructureType,
)


class RiskStructureDetector:
    def __init__(self, classifier: RiskSemanticRoleClassifier | None = None):
        self.classifier = classifier or RiskSemanticRoleClassifier()

    def classify(
        self,
        region: RiskRegion,
        tables: list[DetectedTable],
    ) -> RiskStructureType:
        matching = [
            table for table in tables
            if table.table_id in set(region.table_ids)
        ]
        for table in matching:
            roles = [
                self.classifier.classify(header, is_header=True)
                for header in table.headers
            ]
            content_roles = {
                role for role in roles
                if role not in {RiskSemanticRole.TABLE_HEADER, RiskSemanticRole.OTHER}
            }
            if RiskSemanticRole.RISK_NAME not in content_roles:
                continue
            descriptive = content_roles & {
                RiskSemanticRole.RISK_DESCRIPTION,
                RiskSemanticRole.RISK_CAUSE,
                RiskSemanticRole.RISK_IMPACT,
                RiskSemanticRole.RISK_MITIGATION,
            }
            if not descriptive:
                continue
            width = max([len(table.headers), *(len(row) for row in table.rows)], default=0)
            if width > 2 or len(descriptive) > 1:
                return RiskStructureType.TABLE_MULTI_COL
            return RiskStructureType.TABLE_2COL

        blocks = region.raw_blocks
        short_names = [
            block for block in blocks
            if self.classifier.classify(block.raw_text) == RiskSemanticRole.RISK_NAME
            and len(re.sub(r"\s+", "", block.raw_text)) <= 40
        ]
        if short_names and any(block.bbox for block in short_names):
            return RiskStructureType.VERTICAL_PAIR

        lines = [line.strip() for line in region.raw_text.splitlines() if line.strip()]
        if any(re.match(r"^[-–—ㆍ·▪•▶▷●○□◇※*]", line) and "위험" in line for line in lines):
            return RiskStructureType.BULLET
        if re.search(r"[가-힣A-Za-z0-9() ]+위험\s*[,，;；/]", region.raw_text):
            return RiskStructureType.INLINE
        if sum(
            self.classifier.classify(line) == RiskSemanticRole.RISK_NAME
            for line in lines
        ) >= 1:
            return RiskStructureType.HEADING_PARAGRAPH
        return RiskStructureType.UNKNOWN
