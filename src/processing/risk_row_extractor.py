from __future__ import annotations

import re
from dataclasses import dataclass

from parsers.table_parser import (
    is_semantic_risk_table,
)
from processing.risk_record_assembler import RiskRecordAssembler
from schemas.chunk import Chunk
from schemas.document import DetectedTable
from schemas.risk_extraction import RiskRegion


GENERIC_RISK_HEADINGS = {
    "위험",
    "투자위험",
    "주요투자위험",
    "집합투자기구의투자위험",
    "일반위험",
    "특수위험",
    "기타투자위험",
    "기타위험",
    "투자위험의주요내용",
}


@dataclass(frozen=True)
class RiskCandidate:
    candidate_id: str
    name: str
    description: str
    evidence_refs: tuple[str, ...]
    table_id: str | None = None
    row_index: int | None = None
    source: str = "table"


def compact_risk_text(text: str | None) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text or "")


def is_container_risk_heading(text: str | None) -> bool:
    compact = compact_risk_text(text)
    return (
        compact in GENERIC_RISK_HEADINGS
        or (compact.endswith("위험등") and "," in (text or ""))
    )


def normalize_source_risk_name(text: str | None) -> str:
    value = re.sub(r"\s+", "", text or "").strip(" |-·ㆍ,，;；:：")
    value = re.sub(r"^(?:구분|세부구분|주요투자위험)", "", value)
    return value


def collect_table_risk_candidates(
    chunks: list[Chunk],
    tables: list[DetectedTable] | None,
) -> list[RiskCandidate]:
    selected = sorted(
        (item for item in tables or [] if is_semantic_risk_table(item)),
        key=lambda item: (item.page_number, item.table_id),
    )
    regions = []
    for table in selected:
        refs = list(dict.fromkeys(
            chunk.chunk_id for chunk in chunks if chunk.table_id == table.table_id
        ))
        text = "\n".join(
            chunk.text or "" for chunk in chunks if chunk.table_id == table.table_id
        )
        document_id = chunks[0].document_id if chunks else table.table_id.split("_p", 1)[0]
        regions.append(
            RiskRegion(
                region_id=f"{document_id}:risk-table:{table.table_id}",
                document_id=document_id,
                page_start=table.page_number,
                page_end=table.page_number,
                raw_text=text,
                evidence_refs=refs,
                table_ids=[table.table_id],
                raw_headers=[list(table.raw_headers or table.headers)],
                raw_rows=[[list(row) for row in table.rows]],
            )
        )
    records, _diagnostics = RiskRecordAssembler().assemble(
        regions, selected, chunks
    )
    table_map = {table.table_id: table for table in selected}
    return [
        RiskCandidate(
            candidate_id=record.candidate_id,
            name=record.name,
            description=record.description,
            evidence_refs=tuple(record.evidence_refs),
            table_id=record.name_span.table_id,
            row_index=record.name_span.row_index,
            source=(
                table_map[record.name_span.table_id].extraction_method
                if record.name_span.table_id in table_map
                else "table"
            ),
        )
        for record in records
    ]


def _is_explicit_row(
    name: str,
    description: str,
    refs: tuple[str, ...],
) -> bool:
    compact_name = compact_risk_text(name)
    compact_description = compact_risk_text(description)
    if not compact_name or not compact_description:
        return False
    if is_container_risk_heading(name):
        return False
    if len(compact_name) < 4 or len(compact_name) > 40:
        return False
    if not re.search(r"위험(?:등)?(?:[A-Za-z0-9가-힣()]*)$", compact_name):
        return False
    if len(compact_description) < 20:
        return False
    return True
