from __future__ import annotations

import re

from processing.risk_description_boundary import (
    ProvenanceContext,
    clip_description_body,
    soft_cleanup_description,
)
from processing.risk_semantic_role_classifier import RiskSemanticRoleClassifier
from schemas.risk_extraction import (
    AssembledRiskRecord,
    RiskSemanticRole,
    RiskSourceSpan,
    RiskStructureType,
)


class RiskEvidenceBinder:
    def __init__(self, classifier: RiskSemanticRoleClassifier | None = None):
        self.classifier = classifier or RiskSemanticRoleClassifier()

    @staticmethod
    def normalize_name(raw: str | None) -> str:
        # Layout-only repair: never replace a source term with a semantic synonym.
        value = re.sub(r"\s+", "", raw or "").strip(" |-·ㆍ,，;；:：")
        # A category heading can be glued to the preceding cell by coordinate
        # extraction.  Retain only the source text before that strong boundary.
        value = re.sub(
            r"(?:[가-라]\.)?(?:일반위험|특수위험|기타투자위험|기타위험)(?:등)?$",
            "",
            value,
        )
        return value.strip(" |-·ㆍ,，;；:：")

    @staticmethod
    def normalize_description(parts: list[str]) -> str:
        text = re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()
        return soft_cleanup_description(text)

    def description_from_evidence_span(
        self,
        name: str,
        evidence_text: str | None,
        *,
        provenance: ProvenanceContext | None = None,
    ) -> str | None:
        """Keep only the body between this risk name and the next Hard/Conditional stop.

        Does not create or drop risk records — description binding only.
        Soft punctuation stops are not used here (sanitizer handles residue).
        """
        return clip_description_body(
            name,
            evidence_text,
            provenance=provenance,
            classifier=self.classifier,
            allow_soft=False,
        )

    def bind(
        self,
        *,
        candidate_id: str,
        raw_name: str,
        description_parts: list[tuple[str, RiskSemanticRole, int | None, str | None]],
        structure_type: RiskStructureType,
        page_number: int,
        evidence_refs: list[str],
        table_id: str | None = None,
        row_index: int | None = None,
        name_column_index: int | None = None,
        name_column: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        evidence_text: str | None = None,
        next_row_names: list[str] | None = None,
        next_row_bbox: tuple[float, float, float, float] | None = None,
        section_id: str | None = None,
    ) -> AssembledRiskRecord | None:
        name_role = self.classifier.classify(raw_name)
        name = self.normalize_name(raw_name)
        if name_role in {
            RiskSemanticRole.SECTION_HEADING,
            RiskSemanticRole.RISK_CATEGORY,
            RiskSemanticRole.TABLE_HEADER,
        }:
            return None
        if not name or (
            "위험" not in name
            and self.classifier.classify(name) != RiskSemanticRole.RISK_NAME
        ):
            return None
        description = self.normalize_description([part[0] for part in description_parts])
        if description and evidence_text:
            provenance = ProvenanceContext(
                table_id=table_id,
                section_id=section_id,
                row_index=row_index,
                page_number=page_number,
                bbox=bbox,
                next_row_names=list(next_row_names or []),
                next_row_bbox=next_row_bbox,
                evidence_refs=list(evidence_refs),
                accepted_pages=[page_number],
            )
            clipped = self.description_from_evidence_span(
                name, evidence_text, provenance=provenance
            )
            # Rebind existing descriptions only — never create new risk records.
            if clipped:
                description = clipped
        if not description:
            return None

        name_span = RiskSourceSpan(
            source_id=f"{candidate_id}:name",
            page_number=page_number,
            raw_text=raw_name,
            normalized_text=name,
            semantic_role=RiskSemanticRole.RISK_NAME,
            evidence_refs=list(evidence_refs),
            table_id=table_id,
            row_index=row_index,
            column_index=name_column_index,
            column_name=name_column,
            bbox=bbox,
        )
        description_spans = [
            RiskSourceSpan(
                source_id=f"{candidate_id}:description:{index}",
                page_number=page_number,
                raw_text=raw,
                normalized_text=re.sub(r"\s+", " ", raw).strip(),
                semantic_role=role,
                evidence_refs=list(evidence_refs),
                table_id=table_id,
                row_index=row_index,
                column_index=column_index,
                column_name=column_name,
            )
            for index, (raw, role, column_index, column_name) in enumerate(description_parts)
            if raw.strip()
        ]
        return AssembledRiskRecord(
            candidate_id=candidate_id,
            name=name,
            description=description,
            structure_type=structure_type,
            evidence_refs=list(dict.fromkeys(evidence_refs)),
            name_span=name_span,
            description_spans=description_spans,
        )
