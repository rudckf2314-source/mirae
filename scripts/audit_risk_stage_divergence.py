"""Stage-by-stage investment_risks divergence audit (read-only for production code).

Replays deterministic pipeline stages for the same 100 PDFs and compares:
- in-memory stage counts
- risk_precision_all_after.json (784 report)
- persisted canonical JSON (813)
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from parsers.pdf_parser import PdfParser  # noqa: E402
from processing.chunker import Chunker  # noqa: E402
from processing.final_reconciler import FinalReconciler  # noqa: E402
from processing.json_merger import JsonMerger  # noqa: E402
from processing.narrative_extractor import apply_narrative_facts  # noqa: E402
from processing.post_processor import PostProcessor  # noqa: E402
from processing.section_detector import SectionDetector  # noqa: E402
from schemas.extraction import LLMExtractionResult  # noqa: E402
from schemas.product import CanonicalProduct, DocumentMeta  # noqa: E402
from standardization.schema_mapper import SchemaMapper  # noqa: E402
from utils.hashing import sha256_bytes  # noqa: E402
from validators.persistence_quality_gate import PersistenceQualityGate  # noqa: E402
from validators.pipeline import ValidationPipeline  # noqa: E402
from verification.pipeline import VerificationPipeline  # noqa: E402
from workflows.checkpoint_store import NodeCheckpointStore  # noqa: E402
from workflows.extraction_graph import (  # noqa: E402
    CHUNK_NODE_VERSION,
    PARSE_NODE_VERSION,
    SECTION_NODE_VERSION,
    ChunkCheckpoint,
    SectionCheckpoint,
)
from schemas.document import ParsedDocument  # noqa: E402


STAGES = [
    "1_chain",
    "2_merger",
    "3_postprocessor",
    "4_validator",
    "5_verification",
    "6_final_reconciler",
    "7_quality_gate",
    "8_pre_persist_object",
    "9_disk_canonical",
    "10_schema_mapper_input",
]


def risk_count(product: CanonicalProduct | None) -> int:
    if product is None:
        return 0
    return len(product.product.investment_risks or [])


def risk_names(product: CanonicalProduct | None) -> list[str]:
    if product is None:
        return []
    return [item.name or "" for item in product.product.investment_risks]


def load_disk(document_id: str) -> CanonicalProduct | None:
    path = ROOT / "data" / "cache" / "extracted" / f"{document_id}.json"
    if not path.exists():
        return None
    return CanonicalProduct.model_validate_json(path.read_text(encoding="utf-8"))


def main() -> int:
    pdf_dir = ROOT / "cache"
    if not list(pdf_dir.glob("*.pdf")):
        pdf_dir = ROOT / "data" / "cache" / "pdf"
    docs = sorted({p.stem for p in pdf_dir.glob("*.pdf")})
    assert len(docs) == 100, f"expected 100 PDFs, got {len(docs)} in {pdf_dir}"

    memory_report = json.loads(
        (ROOT / "data" / "cache" / "risk_precision_all_after.json").read_text(encoding="utf-8")
    )
    memory_counts = {
        item["document_id"]: item["risk_count"] for item in memory_report["results"]
    }

    parser = PdfParser()
    detector = SectionDetector()
    chunker = Chunker()
    merger = JsonMerger()
    post = PostProcessor()
    validator = ValidationPipeline()
    verifier = VerificationPipeline()
    reconciler = FinalReconciler()
    gate = PersistenceQualityGate()
    mapper = SchemaMapper()
    checkpoints = NodeCheckpointStore(ROOT / "data" / "cache" / "node_checkpoints")

    stage_totals: dict[str, int] = {stage: 0 for stage in STAGES}
    stage_doc_counts: dict[str, dict[str, int]] = {stage: {} for stage in STAGES}
    stage_names: dict[str, dict[str, list[str]]] = {stage: {} for stage in STAGES}
    meta: dict[str, dict] = {}

    started = time.perf_counter()
    for index, document_id in enumerate(docs, start=1):
        pdf_path = next(pdf_dir.glob(f"{document_id}*.pdf"))
        pdf_bytes = pdf_path.read_bytes()
        document_hash = sha256_bytes(pdf_bytes)

        parsed = checkpoints.load_model(
            document_hash, "parse", PARSE_NODE_VERSION, ParsedDocument, document_id=document_id
        )
        parse_source = "checkpoint" if parsed is not None else "fresh_parse"
        if parsed is None:
            parsed = parser.parse(
                pdf_bytes, file_name=pdf_path.name, document_hash=document_hash, document_id=document_id
            )

        sections_ckpt = checkpoints.load_model(
            document_hash, "sections", SECTION_NODE_VERSION, SectionCheckpoint, document_id=document_id
        )
        sections = sections_ckpt.root if sections_ckpt is not None else detector.detect(parsed)

        chunks_ckpt = checkpoints.load_model(
            document_hash, "chunks", CHUNK_NODE_VERSION, ChunkCheckpoint, document_id=document_id
        )
        chunks = chunks_ckpt.root if chunks_ckpt is not None else chunker.chunk(
            parsed, sections, tables=parsed.tables
        )

        # 1) ProductExtractionChain deterministic narrative (risks source of truth in chain)
        chain_product = CanonicalProduct(
            document=DocumentMeta(
                document_id=document_id,
                document_hash=document_hash,
                file_name=pdf_path.name,
                page_count=parsed.page_count,
            )
        )
        chain_product = apply_narrative_facts(
            chain_product, chunks, parsed.tables, parsed=parsed
        )
        chain_result = LLMExtractionResult(
            product=chain_product.product,
            classes=chain_product.classes,
            ownership=chain_product.extraction.ownership,
            candidate_outcomes=chain_product.extraction.candidate_outcomes,
            risk_diagnostics=chain_product.extraction.risk_diagnostics,
        )

        # 2) JsonMerger
        merged = merger.merge(parsed, chunks, chain_result)

        # 3) PostProcessor (production call: no parsed kwarg)
        after_post = post.process(deepcopy(merged), chunks, tables=parsed.tables)

        # 4) Validator
        after_validate = validator.validate(
            deepcopy(after_post), chunks, tables=parsed.tables
        )

        # 5) Verification (no LLM; verifier does not invent risks)
        after_verify = verifier.verify(
            deepcopy(after_validate), chunks, tables=parsed.tables, llm=None
        )

        # 6) FinalReconciler / RiskHeadingAnchor
        after_reconcile = reconciler.reconcile(
            deepcopy(after_verify), chunks, tables=parsed.tables
        )

        # 7) PersistenceQualityGate (check only; object unchanged)
        after_gate = deepcopy(after_reconcile)
        gate_blockers: list[str] = []
        try:
            gate.check(
                after_gate,
                chunks,
                expected_document_hash=document_hash,
                tables=parsed.tables,
            )
        except Exception as exc:  # noqa: BLE001 - audit must continue
            gate_blockers = [str(exc)]

        # 8) Pre-persist object (same as after gate)
        pre_persist = after_gate

        # 9) Disk canonical
        disk = load_disk(document_id)

        # 10) SchemaMapper input (= pre_persist object that would be saved)
        schema_input = deepcopy(pre_persist)
        try:
            mapper.map(schema_input)
        except Exception:
            pass

        values = {
            "1_chain": chain_product,
            "2_merger": merged,
            "3_postprocessor": after_post,
            "4_validator": after_validate,
            "5_verification": after_verify,
            "6_final_reconciler": after_reconcile,
            "7_quality_gate": after_gate,
            "8_pre_persist_object": pre_persist,
            "9_disk_canonical": disk,
            "10_schema_mapper_input": schema_input,
        }
        for stage, product in values.items():
            count = risk_count(product)
            stage_totals[stage] += count
            stage_doc_counts[stage][document_id] = count
            stage_names[stage][document_id] = risk_names(product)

        disk_count = risk_count(disk)
        meta[document_id] = {
            "parse_source": parse_source,
            "memory_report_count": memory_counts.get(document_id),
            "disk_count": disk_count,
            "chain_count": risk_count(chain_product),
            "post_count": risk_count(after_post),
            "reconcile_count": risk_count(after_reconcile),
            "pre_persist_count": risk_count(pre_persist),
            "gate_blockers": gate_blockers,
            "disk_minus_memory_report": (
                None
                if memory_counts.get(document_id) is None
                else disk_count - memory_counts[document_id]
            ),
            "disk_minus_pre_persist": disk_count - risk_count(pre_persist),
            "chain_names": risk_names(chain_product),
            "post_names": risk_names(after_post),
            "reconcile_names": risk_names(after_reconcile),
            "disk_names": risk_names(disk),
        }
        print(
            f"[{index}/100] {document_id} "
            f"chain={risk_count(chain_product)} post={risk_count(after_post)} "
            f"reconcile={risk_count(after_reconcile)} disk={disk_count} "
            f"mem_report={memory_counts.get(document_id)} parse={parse_source}",
            flush=True,
        )

    # Stage deltas
    deltas = []
    for left, right in zip(STAGES, STAGES[1:]):
        increased = []
        decreased = []
        for document_id in docs:
            before = stage_doc_counts[left][document_id]
            after = stage_doc_counts[right][document_id]
            if after > before:
                increased.append(
                    {
                        "document_id": document_id,
                        "before": before,
                        "after": after,
                        "delta": after - before,
                        "added": sorted(
                            set(stage_names[right][document_id])
                            - set(stage_names[left][document_id])
                        ),
                        "removed": sorted(
                            set(stage_names[left][document_id])
                            - set(stage_names[right][document_id])
                        ),
                    }
                )
            elif after < before:
                decreased.append(
                    {
                        "document_id": document_id,
                        "before": before,
                        "after": after,
                        "delta": after - before,
                        "added": sorted(
                            set(stage_names[right][document_id])
                            - set(stage_names[left][document_id])
                        ),
                        "removed": sorted(
                            set(stage_names[left][document_id])
                            - set(stage_names[right][document_id])
                        ),
                    }
                )
        deltas.append(
            {
                "from": left,
                "to": right,
                "total_before": stage_totals[left],
                "total_after": stage_totals[right],
                "net_delta": stage_totals[right] - stage_totals[left],
                "increased_docs": len(increased),
                "decreased_docs": len(decreased),
                "increased": increased,
                "decreased": decreased,
            }
        )

    disk_vs_memory = [
        {
            "document_id": document_id,
            "memory_report": memory_counts.get(document_id),
            "disk": stage_doc_counts["9_disk_canonical"][document_id],
            "pre_persist_replay": stage_doc_counts["8_pre_persist_object"][document_id],
            "chain_replay": stage_doc_counts["1_chain"][document_id],
            "delta_disk_memory": stage_doc_counts["9_disk_canonical"][document_id]
            - (memory_counts.get(document_id) or 0),
            "delta_disk_pre_persist": stage_doc_counts["9_disk_canonical"][document_id]
            - stage_doc_counts["8_pre_persist_object"][document_id],
            "disk_only_names": sorted(
                set(stage_names["9_disk_canonical"][document_id])
                - set(stage_names["8_pre_persist_object"][document_id])
            ),
            "replay_only_names": sorted(
                set(stage_names["8_pre_persist_object"][document_id])
                - set(stage_names["9_disk_canonical"][document_id])
            ),
            "memory_report_only_vs_disk": sorted(
                set(memory_report_names(memory_report, document_id))
                - set(stage_names["9_disk_canonical"][document_id])
            ),
            "disk_only_vs_memory_report": sorted(
                set(stage_names["9_disk_canonical"][document_id])
                - set(memory_report_names(memory_report, document_id))
            ),
        }
        for document_id in docs
        if (
            stage_doc_counts["9_disk_canonical"][document_id]
            != (memory_counts.get(document_id) or -1)
            or stage_doc_counts["9_disk_canonical"][document_id]
            != stage_doc_counts["8_pre_persist_object"][document_id]
        )
    ]

    summary = {
        "documents": len(docs),
        "elapsed_sec": round(time.perf_counter() - started, 1),
        "stage_totals": stage_totals,
        "memory_report_total": sum(memory_counts.values()),
        "disk_total": stage_totals["9_disk_canonical"],
        "replay_pre_persist_total": stage_totals["8_pre_persist_object"],
        "stage_deltas": deltas,
        "divergence_docs_disk_vs_memory_or_replay": len(disk_vs_memory),
        "disk_vs_memory_or_replay": disk_vs_memory,
        "document_meta": meta,
        "notes": [
            "Stage 1 uses ProductExtractionChain's deterministic narrative path (apply_narrative_facts with parsed=).",
            "Stage 3 mirrors PostProcessor production call (apply_narrative_facts without parsed).",
            "Stage 5 runs VerificationPipeline with llm=None; verifier does not mutate risk lists.",
            "Stage 9 reads existing data/cache/extracted/*.json without overwrite.",
            "No production extraction code was modified by this audit.",
        ],
    }

    out = ROOT / "data" / "cache" / "risk_stage_divergence_audit.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "stage_totals": stage_totals,
        "memory_report_total": summary["memory_report_total"],
        "disk_total": summary["disk_total"],
        "replay_pre_persist_total": summary["replay_pre_persist_total"],
        "net_deltas": [
            {
                "from": item["from"],
                "to": item["to"],
                "net_delta": item["net_delta"],
                "increased_docs": item["increased_docs"],
                "decreased_docs": item["decreased_docs"],
            }
            for item in deltas
        ],
        "output": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


def memory_report_names(report: dict, document_id: str) -> list[str]:
    for item in report["results"]:
        if item["document_id"] == document_id:
            return list(item.get("risk_names") or [])
    return []


if __name__ == "__main__":
    raise SystemExit(main())
