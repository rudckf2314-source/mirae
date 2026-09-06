import hashlib

from schemas.chunk import Chunk
from schemas.product import CanonicalProduct, EvidenceItem


class SourceValidator:
    def validate(self, product: CanonicalProduct, chunks: list[Chunk]) -> tuple[list[str], CanonicalProduct]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        valid_ids = set(chunk_map)
        warnings: list[str] = []
        cleaned = product.model_copy(deep=True)

        def filter_refs(refs: list[str], location: str) -> list[str]:
            kept: list[str] = []
            for ref in refs:
                if ref in valid_ids:
                    kept.append(ref)
                else:
                    warnings.append(f"invalid evidence_ref at {location}: {ref}")
            return kept

        cleaned.product.risk.evidence_refs = filter_refs(cleaned.product.risk.evidence_refs, "product.risk")
        cleaned.product.investment_objective.evidence_refs = filter_refs(
            cleaned.product.investment_objective.evidence_refs,
            "product.investment_objective",
        )
        cleaned.product.investment_strategy.evidence_refs = filter_refs(
            cleaned.product.investment_strategy.evidence_refs,
            "product.investment_strategy",
        )
        for index, item in enumerate(cleaned.product.investment_risks):
            item.evidence_refs = filter_refs(item.evidence_refs, f"product.investment_risks[{index}]")
        for index, item in enumerate(cleaned.classes):
            item.evidence_refs = filter_refs(item.evidence_refs, f"classes[{index}]")
        for index, item in enumerate(cleaned.fees):
            item.evidence_refs = filter_refs(item.evidence_refs, f"fees[{index}]")
        for index, item in enumerate(cleaned.performance):
            item.evidence_refs = filter_refs(item.evidence_refs, f"performance[{index}]")
        for index, item in enumerate(cleaned.aum):
            item.evidence_refs = filter_refs(item.evidence_refs, f"aum[{index}]")

        valid_used = set(cleaned.all_evidence_refs())
        existing = {item.chunk_id: item for item in cleaned.evidence}
        # Post-processing can introduce deterministic facts after JsonMerger has
        # built its initial evidence list. Materialize every surviving reference
        # from the authoritative chunk map so canonical refs never dangle.
        cleaned.evidence = []
        for chunk_id in sorted(valid_used):
            item = existing.get(chunk_id)
            if item is not None:
                chunk = chunk_map[chunk_id]
                # Replace old preview/truncated evidence with the full source.
                source_text = chunk.page_source_text if chunk.table_id and chunk.page_source_text else chunk.text
                cleaned.evidence.append(
                    item.model_copy(update={
                        "source_text": source_text,
                        "table_markdown": chunk.text if chunk.table_id else None,
                        "source_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    })
                )
                continue
            chunk = chunk_map[chunk_id]
            source_text = chunk.page_source_text if chunk.table_id and chunk.page_source_text else chunk.text
            cleaned.evidence.append(
                EvidenceItem(
                    chunk_id=chunk.chunk_id,
                    document_id=cleaned.document.document_id,
                    file_name=cleaned.document.file_name,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section_type=chunk.section_type.value,
                    source_text=source_text,
                    table_markdown=chunk.text if chunk.table_id else None,
                    source_hash=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    table_id=chunk.table_id,
                )
            )
        return warnings, cleaned
