from __future__ import annotations

import re

from exceptions import DeterminismConflictError, QualityGateError
from processing.narrative_extractor import is_strategy_contaminated, recover_objective_from_chunks
from processing.risk_heading_anchor import is_generic_risk_heading
from parsers.table_parser import is_semantic_risk_table
from schemas.chunk import Chunk
from schemas.document import DetectedTable
from schemas.product import CanonicalProduct


class PersistenceQualityGate:
    """Fail closed on deterministic defects immediately before persistence."""

    def check(
        self,
        product: CanonicalProduct,
        chunks: list[Chunk],
        *,
        expected_document_hash: str,
        current_fingerprint: str,
        previous_fingerprint: str | None = None,
        tables: list[DetectedTable] | None = None,
    ) -> None:
        if previous_fingerprint and previous_fingerprint != current_fingerprint:
            raise DeterminismConflictError(
                expected_document_hash, previous_fingerprint, current_fingerprint
            )

        blockers: list[str] = []
        if product.document.document_hash != expected_document_hash:
            blockers.append("DOCUMENT_HASH_MISMATCH")

        objective = product.product.investment_objective
        if not (objective.text or "").strip() and recover_objective_from_chunks(chunks) is not None:
            blockers.append("OBJECTIVE_SOURCE_CANDIDATE_DROPPED")

        strategy = product.product.investment_strategy.text
        if strategy and is_strategy_contaminated(strategy):
            blockers.append("STRATEGY_CONTAMINATED")

        chunk_map = {chunk.chunk_id: chunk.text or "" for chunk in chunks}
        for index, risk in enumerate(product.product.investment_risks):
            name = risk.name or ""
            if not name.strip():
                blockers.append(f"RISK_NAME_EMPTY[{index}]")
            if any(character in name for character in ("\n", "\r", "\t")):
                blockers.append(f"RISK_NAME_LAYOUT_WHITESPACE[{index}]")
            if is_generic_risk_heading(name):
                blockers.append(f"RISK_SECTION_HEADING[{index}]")
            evidence = "".join(chunk_map.get(ref, "") for ref in risk.evidence_refs)
            compact_name = re.sub(r"[^가-힣A-Za-z0-9]", "", name)
            compact_evidence = re.sub(r"[^가-힣A-Za-z0-9]", "", evidence)
            if not risk.evidence_refs or not compact_name or compact_name not in compact_evidence:
                blockers.append(f"RISK_NAME_NOT_SOURCE_ANCHORED[{index}]")

        if any(is_semantic_risk_table(table) for table in tables or []):
            if not product.product.investment_risks:
                blockers.append("RISK_TABLE_PRESENT_BUT_EMPTY")

        for item in product.extraction.verification.items:
            if item.field_path.startswith("performance[") and item.status == "FAIL":
                blockers.append(f"PERFORMANCE_EVIDENCE_FAIL:{item.field_path}")

        for field, items in (("fees", product.fees), ("performance", product.performance)):
            for index, item in enumerate(items):
                if not item.evidence_refs:
                    blockers.append(f"TABLE_FACT_MISSING_EVIDENCE:{field}[{index}]")

        if blockers:
            raise QualityGateError(list(dict.fromkeys(blockers)))
