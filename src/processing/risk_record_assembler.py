from __future__ import annotations

import re

from parsers.table_parser import reconstruct_risk_table
from processing.risk_evidence_binder import RiskEvidenceBinder
from processing.risk_semantic_role_classifier import RiskSemanticRoleClassifier
from processing.risk_structure_detector import RiskStructureDetector
from schemas.chunk import Chunk
from schemas.document import DetectedTable
from schemas.risk_extraction import (
    AssembledRiskRecord,
    RiskRegion,
    RiskSemanticRole,
    RiskStructureType,
    UnknownRiskTemplateDiagnostic,
)


DESCRIPTION_ROLES = {
    RiskSemanticRole.RISK_DESCRIPTION,
    RiskSemanticRole.RISK_CAUSE,
    RiskSemanticRole.RISK_IMPACT,
    RiskSemanticRole.RISK_MITIGATION,
}


class RiskRecordAssembler:
    def __init__(
        self,
        classifier: RiskSemanticRoleClassifier | None = None,
        detector: RiskStructureDetector | None = None,
        binder: RiskEvidenceBinder | None = None,
    ):
        self.classifier = classifier or RiskSemanticRoleClassifier()
        self.detector = detector or RiskStructureDetector(self.classifier)
        self.binder = binder or RiskEvidenceBinder(self.classifier)

    def assemble(
        self,
        regions: list[RiskRegion],
        tables: list[DetectedTable],
        chunks: list[Chunk],
    ) -> tuple[list[AssembledRiskRecord], list[UnknownRiskTemplateDiagnostic]]:
        records: list[AssembledRiskRecord] = []
        diagnostics: list[UnknownRiskTemplateDiagnostic] = []
        refs_by_table = {
            table_id: list(dict.fromkeys(
                chunk.chunk_id for chunk in chunks if chunk.table_id == table_id
            ))
            for table_id in {table.table_id for table in tables}
        }
        evidence_by_ref = {
            chunk.chunk_id: chunk.text or ""
            for chunk in chunks
            if chunk.chunk_id
        }
        table_map = {table.table_id: table for table in tables}

        for region in regions:
            structure = self.detector.classify(region, tables)
            before = len(records)
            if structure in {
                RiskStructureType.TABLE_2COL,
                RiskStructureType.TABLE_MULTI_COL,
            }:
                for table_id in region.table_ids:
                    table = table_map.get(table_id)
                    if table:
                        records.extend(
                            self._assemble_table(
                                table,
                                structure,
                                refs_by_table.get(table_id, []),
                                evidence_by_ref,
                            )
                        )
            elif structure == RiskStructureType.VERTICAL_PAIR:
                records.extend(
                    self._assemble_vertical(region, structure, evidence_by_ref)
                )
            elif structure in {
                RiskStructureType.HEADING_PARAGRAPH,
                RiskStructureType.BULLET,
                RiskStructureType.INLINE,
            }:
                records.extend(
                    self._assemble_text(region, structure, evidence_by_ref)
                )

            if len(records) == before:
                diagnostics.append(
                    UnknownRiskTemplateDiagnostic(
                        document_id=region.document_id,
                        page=region.page_start,
                        structure_type=structure,
                        failure_stage=(
                            "RiskStructureDetector"
                            if structure == RiskStructureType.UNKNOWN
                            else "RiskRecordAssembler"
                        ),
                        reason="Risk region detected but no explicit source-backed record was assembled.",
                        raw_text=region.raw_text,
                        raw_headers=(region.raw_headers[0] if region.raw_headers else []),
                        raw_table=(region.raw_rows[0] if region.raw_rows else []),
                        raw_blocks=region.raw_blocks,
                        evidence_refs=region.evidence_refs,
                    )
                )
        return self._dedupe(records), diagnostics

    def _assemble_table(
        self,
        table: DetectedTable,
        structure: RiskStructureType,
        refs: list[str],
        evidence_by_ref: dict[str, str] | None = None,
    ) -> list[AssembledRiskRecord]:
        roles = [
            self.classifier.classify(header, is_header=True)
            for header in table.headers
        ]
        try:
            name_index = roles.index(RiskSemanticRole.RISK_NAME)
        except ValueError:
            return []
        description_indexes = [
            index for index, role in enumerate(roles) if role in DESCRIPTION_ROLES
        ]
        if not description_indexes:
            return []
        if self._has_wrapped_names(table, name_index):
            normalized = reconstruct_risk_table(
                [table.headers, *table.rows], table.table_id, table.page_number
            )
            if normalized is not None:
                table = normalized
                roles = [
                    self.classifier.classify(header, is_header=True)
                    for header in table.headers
                ]
                name_index = roles.index(RiskSemanticRole.RISK_NAME)
                description_indexes = [
                    index for index, role in enumerate(roles) if role in DESCRIPTION_ROLES
                ]
        evidence_text = self._join_evidence(refs, evidence_by_ref)
        records: list[AssembledRiskRecord] = []
        for row_index, row in enumerate(table.rows):
            raw_name = row[name_index] if name_index < len(row) else ""
            parts = [
                (
                    row[index] if index < len(row) else "",
                    roles[index],
                    index,
                    table.headers[index] if index < len(table.headers) else None,
                )
                for index in description_indexes
            ]
            next_names: list[str] = []
            for later in table.rows[row_index + 1 :]:
                later_name = later[name_index] if name_index < len(later) else ""
                if later_name.strip():
                    next_names.append(later_name.strip())
            record = self.binder.bind(
                candidate_id=f"risk-row:{table.table_id}:{row_index}",
                raw_name=raw_name,
                description_parts=parts,
                structure_type=structure,
                page_number=table.page_number,
                evidence_refs=refs,
                table_id=table.table_id,
                row_index=row_index,
                name_column_index=name_index,
                name_column=table.headers[name_index],
                evidence_text=evidence_text,
                next_row_names=next_names,
                section_id="INVESTMENT_RISK",
            )
            if record:
                records.append(record)
        return records

    @staticmethod
    def _join_evidence(
        refs: list[str],
        evidence_by_ref: dict[str, str] | None,
        fallback: str = "",
    ) -> str:
        if not evidence_by_ref:
            return fallback
        parts = [evidence_by_ref.get(ref, "") for ref in refs]
        joined = "\n".join(part for part in parts if part)
        return joined or fallback

    @staticmethod
    def _has_wrapped_names(table: DetectedTable, name_index: int) -> bool:
        names = [
            row[name_index].strip() if name_index < len(row) else ""
            for row in table.rows
        ]
        for index, name in enumerate(names[:-1]):
            if not name or "위험" in re.sub(r"\s+", "", name):
                continue
            following = names[index + 1]
            if following and "위험" in re.sub(r"\s+", "", f"{name}{following}"):
                return True
        return False

    def _assemble_vertical(
        self,
        region: RiskRegion,
        structure: RiskStructureType,
        evidence_by_ref: dict[str, str] | None = None,
    ) -> list[AssembledRiskRecord]:
        blocks = sorted(
            region.raw_blocks,
            key=lambda block: (
                block.bbox[1] if block.bbox else 0,
                block.bbox[0] if block.bbox else 0,
            ),
        )
        records: list[AssembledRiskRecord] = []
        for index, block in enumerate(blocks):
            if self.classifier.classify(block.raw_text) != RiskSemanticRole.RISK_NAME:
                continue
            following: list[str] = []
            next_names: list[str] = []
            next_bbox = None
            for candidate in blocks[index + 1 :]:
                role = self.classifier.classify(candidate.raw_text)
                if role in {
                    RiskSemanticRole.RISK_NAME,
                    RiskSemanticRole.SECTION_HEADING,
                    RiskSemanticRole.RISK_CATEGORY,
                }:
                    if role == RiskSemanticRole.RISK_NAME:
                        next_names.append(candidate.raw_text)
                        next_bbox = candidate.bbox
                    break
                following.append(candidate.raw_text)
            refs = block.evidence_refs or region.evidence_refs
            record = self.binder.bind(
                candidate_id=f"risk-block:{region.region_id}:{index}",
                raw_name=block.raw_text,
                description_parts=[
                    (text, RiskSemanticRole.RISK_DESCRIPTION, None, None)
                    for text in following
                ],
                structure_type=structure,
                page_number=region.page_start,
                evidence_refs=refs,
                bbox=block.bbox,
                evidence_text=self._join_evidence(
                    refs, evidence_by_ref, region.raw_text
                ),
                next_row_names=next_names,
                next_row_bbox=next_bbox,
                section_id="INVESTMENT_RISK",
            )
            if record:
                records.append(record)
        return records

    def _assemble_text(
        self,
        region: RiskRegion,
        structure: RiskStructureType,
        evidence_by_ref: dict[str, str] | None = None,
    ) -> list[AssembledRiskRecord]:
        if structure == RiskStructureType.INLINE:
            return self._assemble_inline(region, evidence_by_ref)
        lines = [line.strip() for line in region.raw_text.splitlines() if line.strip()]
        records: list[AssembledRiskRecord] = []
        current_name = ""
        description: list[str] = []
        sequence = 0
        evidence_text = self._join_evidence(
            region.evidence_refs, evidence_by_ref, region.raw_text
        )

        def flush() -> None:
            nonlocal current_name, description, sequence
            if not current_name:
                return
            record = self.binder.bind(
                candidate_id=f"risk-text:{region.region_id}:{sequence}",
                raw_name=current_name,
                description_parts=[
                    (text, RiskSemanticRole.RISK_DESCRIPTION, None, None)
                    for text in description
                ],
                structure_type=structure,
                page_number=region.page_start,
                evidence_refs=region.evidence_refs,
                evidence_text=evidence_text,
            )
            if record:
                records.append(record)
                sequence += 1
            current_name = ""
            description = []

        for line in lines:
            cleaned = re.sub(r"^[-–—ㆍ·▪•▶▷●○□◇※*]\s*", "", line)
            role = self.classifier.classify(cleaned)
            if role == RiskSemanticRole.RISK_NAME and len(re.sub(r"\s+", "", cleaned)) <= 40:
                flush()
                current_name = cleaned
            elif role in {
                RiskSemanticRole.SECTION_HEADING,
                RiskSemanticRole.RISK_CATEGORY,
                RiskSemanticRole.TABLE_HEADER,
            }:
                flush()
            elif current_name:
                description.append(cleaned)
        flush()
        return records

    def _assemble_inline(
        self,
        region: RiskRegion,
        evidence_by_ref: dict[str, str] | None = None,
    ) -> list[AssembledRiskRecord]:
        names = [
            match.group(1).strip()
            for match in re.finditer(
                r"([가-힣A-Za-z0-9()·ㆍ\- ]{2,40}?위험)(?=\s*[,，;；/]|\s+등|$)",
                region.raw_text,
            )
        ]
        evidence_text = self._join_evidence(
            region.evidence_refs, evidence_by_ref, region.raw_text
        )
        records: list[AssembledRiskRecord] = []
        for index, name in enumerate(names):
            record = self.binder.bind(
                candidate_id=f"risk-inline:{region.region_id}:{index}",
                raw_name=name,
                description_parts=[
                    (
                        region.raw_text,
                        RiskSemanticRole.RISK_DESCRIPTION,
                        None,
                        None,
                    )
                ],
                structure_type=RiskStructureType.INLINE,
                page_number=region.page_start,
                evidence_refs=region.evidence_refs,
                evidence_text=evidence_text,
            )
            if record:
                records.append(record)
        return records

    @staticmethod
    def _dedupe(records: list[AssembledRiskRecord]) -> list[AssembledRiskRecord]:
        kept: list[AssembledRiskRecord] = []
        for record in records:
            name = re.sub(r"\s+", "", record.name)
            description = re.sub(r"\s+", "", record.description)
            duplicate = next(
                (
                    item
                    for item in kept
                    if re.sub(r"\s+", "", item.name) == name
                    and (
                        description == re.sub(r"\s+", "", item.description)
                        or description in re.sub(r"\s+", "", item.description)
                        or re.sub(r"\s+", "", item.description) in description
                    )
                ),
                None,
            )
            if duplicate is not None:
                duplicate.evidence_refs = list(dict.fromkeys(
                    [*duplicate.evidence_refs, *record.evidence_refs]
                ))
                duplicate.description_spans.extend(
                    span for span in record.description_spans
                    if span.source_id not in {item.source_id for item in duplicate.description_spans}
                )
                continue
            kept.append(record)
        return kept
