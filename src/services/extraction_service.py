from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from chains.product_extraction_chain import ProductExtractionChain
from config.settings import Settings, get_settings
from exceptions import DuplicateDocumentError, EmptyPdfError, ProspectusRejectedError
from llm import get_llm_provider
from parsers.pdf_parser import PdfParser
from processing.chunker import Chunker
from processing.json_merger import JsonMerger
from processing.post_processor import PostProcessor
from processing.final_reconciler import FinalReconciler
from processing.progress import ProgressCallback, emit
from processing.section_detector import SectionDetector
from repositories import get_repository
from repositories.base import ProductRepository
from schemas.document import ParsedDocument
from schemas.product import CanonicalProduct, NodeRunMetric
from schemas.product_schema import DocumentType, ProductExtraction
from standardization import SchemaMapper, StandardJsonStore
from database import PostgresStandardStore
from utils.hashing import sanitize_document_id, sha256_bytes
from utils.determinism import (
    FINGERPRINT_VERSION,
    attach_fingerprint,
    canonical_fact_fingerprint,
    stored_fingerprint,
)
from validators.pipeline import ValidationPipeline
from validators.persistence_quality_gate import PersistenceQualityGate
from validators.prospectus_guardrail import assert_investment_prospectus
from verification.pipeline import VerificationPipeline
from workflows.extraction_graph import ExtractionGraph
from workflows.checkpoint_store import NodeCheckpointStore


@dataclass
class ProcessResult:
    product: CanonicalProduct | None
    parsed: ParsedDocument | None
    cached: bool
    duplicate: bool = False
    error: str | None = None
    standardized: ProductExtraction | None = None
    standard_json_path: str | None = None
    db_saved: bool = False
    db_error: str | None = None


class ExtractionService:
    def __init__(
        self,
        settings: Settings | None = None,
        repository: ProductRepository | None = None,
        parser: PdfParser | None = None,
        detector: SectionDetector | None = None,
        chunker: Chunker | None = None,
        chain: ProductExtractionChain | None = None,
        merger: JsonMerger | None = None,
        validator: ValidationPipeline | None = None,
        post_processor: PostProcessor | None = None,
        verifier: VerificationPipeline | None = None,
        final_reconciler: FinalReconciler | None = None,
        schema_mapper: SchemaMapper | None = None,
        quality_gate: PersistenceQualityGate | None = None,
    ):
        self.settings = settings or get_settings()
        self.repository = repository or get_repository(self.settings)
        self.parser = parser or PdfParser()
        self.detector = detector or SectionDetector()
        self.chunker = chunker or Chunker()
        self.merger = merger or JsonMerger()
        self.post_processor = post_processor or PostProcessor()
        self.validator = validator or ValidationPipeline()
        self.verifier = verifier or VerificationPipeline()
        self.final_reconciler = final_reconciler or FinalReconciler()
        self.schema_mapper = schema_mapper or SchemaMapper()
        self.quality_gate = quality_gate or PersistenceQualityGate()
        self.standard_json_store = StandardJsonStore(self.settings.standard_json_dir)
        self.node_checkpoints = NodeCheckpointStore(self.settings.checkpoint_dir)
        self._chain = chain
        self._graph: ExtractionGraph | None = None

    @property
    def chain(self) -> ProductExtractionChain:
        if self._chain is None:
            provider = get_llm_provider(self.settings)
            self._chain = ProductExtractionChain(
                llm=provider.get_chat_model(),
                max_retries=self.settings.llm_max_retries,
                merger=self.merger,
                enable_semantic_review=self.settings.semantic_review_enabled,
                fail_fast_on_llm_error=self.settings.llm_fail_fast,
                checkpoint_store=self.node_checkpoints,
            )
        return self._chain

    @property
    def graph(self) -> ExtractionGraph:
        if self._graph is None:
            if hasattr(self.chain, "checkpoint_store"):
                self.chain.checkpoint_store = self.node_checkpoints
            self._graph = ExtractionGraph(
                parser=self.parser,
                detector=self.detector,
                chunker=self.chunker,
                chain=self.chain,
                merger=self.merger,
                post_processor=self.post_processor,
                validator=self.validator,
                verifier=self.verifier,
                final_reconciler=self.final_reconciler,
                checkpoint_store=self.node_checkpoints,
            )
        return self._graph

    def list_cached(self) -> list[dict]:
        return self.repository.list_products()

    def get_product(self, document_id: str) -> CanonicalProduct | None:
        return self.repository.get_by_document_id(document_id)

    def get_parsed(self, document_id: str) -> ParsedDocument | None:
        return self.repository.get_parsed(document_id)

    def process_pdf(
        self,
        pdf: str | Path | bytes,
        file_name: str | None = None,
        progress_callback: ProgressCallback | None = None,
        force: bool = False,
        force_reprocess: bool | None = None,
    ) -> ProcessResult:
        run_started = time.perf_counter()
        if force_reprocess:
            force = True
        emit(progress_callback, "upload", "PDF 업로드", status="started")
        pdf_bytes, resolved_name = self._load_bytes(pdf, file_name)
        emit(progress_callback, "upload", f"업로드 완료: {resolved_name}", file_name=resolved_name)

        emit(progress_callback, "hash_check", "SHA-256 중복 검사", status="started")
        document_hash = sha256_bytes(pdf_bytes)
        existing = self.repository.get_by_hash(document_hash)
        if existing and not force and self._is_current_schema(existing):
            emit(
                progress_callback,
                "hash_check",
                "이미 처리된 문서입니다.",
                document_id=existing.document.document_id,
            )
            emit(progress_callback, "complete", "기존 Canonical JSON을 불러왔습니다.", cached=True)
            parsed = self.repository.get_parsed(existing.document.document_id)
            standardized, standard_path, db_saved, db_error = self._standardize_and_persist(existing)
            return ProcessResult(
                product=existing, parsed=parsed, cached=True, duplicate=True,
                standardized=standardized, standard_json_path=standard_path,
                db_saved=db_saved, db_error=db_error,
            )
        if existing and not force:
            emit(
                progress_callback,
                "hash_check",
                f"기존 schema_version={existing.schema_version} → {self.settings.schema_version} 재처리",
            )

        emit(progress_callback, "hash_check", f"신규 문서 hash={document_hash[:12]}")
        emit(progress_callback, "guardrail", "투자설명서 구조 가드레일", status="started")
        try:
            assert_investment_prospectus(pdf_bytes, resolved_name)
        except ProspectusRejectedError as exc:
            emit(progress_callback, "guardrail", str(exc), status="failed")
            return ProcessResult(
                product=None,
                parsed=None,
                cached=False,
                error=str(exc),
            )
        emit(progress_callback, "guardrail", "투자설명서 구조 확인")
        document_id = self._resolve_document_id(resolved_name, document_hash)

        graph_state = self.graph.invoke({
            "pdf_bytes": pdf_bytes,
            "file_name": resolved_name,
            "document_id": document_id,
            "document_hash": document_hash,
            "progress_callback": progress_callback,
        })
        parsed = graph_state["parsed"]
        canonical = graph_state["canonical"]

        fingerprint = canonical_fact_fingerprint(canonical)
        previous_fingerprint = stored_fingerprint(existing)
        gate_started = time.perf_counter()
        emit(progress_callback, "quality_gate", "저장 전 품질 게이트", status="started")
        self.quality_gate.check(
            canonical,
            graph_state["chunks"],
            expected_document_hash=document_hash,
            current_fingerprint=fingerprint,
            # force re-extract intentionally rewrites canonical facts.
            previous_fingerprint=None if force else previous_fingerprint,
            tables=parsed.tables,
        )
        emit(progress_callback, "quality_gate", "저장 전 품질 게이트 PASS")
        attach_fingerprint(canonical, fingerprint)
        report = canonical.extraction.run_report
        report.document_hash = document_hash
        report.fingerprint_version = FINGERPRINT_VERSION
        report.canonical_fingerprint = fingerprint
        report.nodes = [
            *report.nodes,
            *graph_state.get("node_metrics", []),
            NodeRunMetric(
                node="quality_gate",
                duration_ms=round((time.perf_counter() - gate_started) * 1000, 3),
            ),
        ]
        report.total_duration_ms = round((time.perf_counter() - run_started) * 1000, 3)
        report.cache_hits = sum(1 for item in report.nodes if item.cache_hit)
        report.llm_calls = sum(item.llm_calls for item in report.nodes)

        emit(progress_callback, "saving", "Cache Save", status="started")
        saved = self.repository.save_product(canonical, pdf_bytes=pdf_bytes, parsed=parsed)
        emit(progress_callback, "saving", "Persistent Cache 저장 완료")

        emit(progress_callback, "standardizing", "Schema v0.1 변환/검증", status="started")
        standardized, standard_path, db_saved, db_error = self._standardize_and_persist(saved)
        emit(progress_callback, "standardizing", "Standard JSON 저장 완료", path=standard_path)
        if self.settings.db_auto_save and self.settings.database_url:
            emit(progress_callback, "database", "PostgreSQL 자동 저장", status="started")
            emit(progress_callback, "database", "DB 저장 완료" if db_saved else f"DB 저장 실패: {db_error}")

        emit(progress_callback, "complete", "Complete", document_id=saved.document.document_id)
        return ProcessResult(
            product=saved, parsed=parsed, cached=False, duplicate=False,
            standardized=standardized, standard_json_path=standard_path,
            db_saved=db_saved, db_error=db_error,
        )


    def _standardize_and_persist(
        self, product: CanonicalProduct
    ) -> tuple[ProductExtraction | None, str | None, bool, str | None]:
        try:
            standardized = self.schema_mapper.map(product)
            if standardized.source_document.document_type != DocumentType.INVESTMENT_PROSPECTUS:
                return None, None, False, "투자설명서 스키마(document_type)가 아니어서 적재를 차단했습니다."
        except Exception as exc:
            return None, None, False, f"SchemaMapper 실패: {exc}"
        path = self.standard_json_store.save(product.document.document_id, standardized)
        db_saved = False
        db_error: str | None = None
        if self.settings.db_auto_save:
            verification = product.extraction.verification
            contradicted = any(
                item.status == "FAIL" or item.verdict == "CONTRADICTED"
                for item in verification.items
            )
            blocked_fields = {
                field: status.value
                for field, status in standardized.field_status.items()
                if field in {"classes", "fees", "sales_charges"}
                and status.value in {"AMBIGUOUS", "CONFLICT", "PARSE_FAILED"}
            }
            if verification.status == "FAIL" or contradicted:
                db_error = (
                    "DB 적재 차단: verification FAIL/CONTRADICTED 결과가 있습니다. "
                    f"fail_count={verification.fail_count}"
                )
            elif blocked_fields:
                db_error = f"DB 적재 차단: 핵심 필드가 확정되지 않았습니다. {blocked_fields}"
            elif not self.settings.database_url:
                db_error = "DATABASE_URL 미설정: Standard JSON까지만 저장되었습니다."
            else:
                try:
                    store = PostgresStandardStore(self.settings.database_url)
                    store.save(product.document.document_id, standardized)
                    db_saved = True
                except Exception as exc:
                    db_error = str(exc)
        return standardized, str(path), db_saved, db_error

    def process_many(
        self,
        items: list[tuple[bytes, str]],
        progress_callback: ProgressCallback | None = None,
        force: bool = False,
        force_reprocess: bool | None = None,
    ) -> list[ProcessResult]:
        if force_reprocess:
            force = True
        results: list[ProcessResult] = []
        for pdf_bytes, file_name in items:
            try:
                results.append(
                    self.process_pdf(
                        pdf_bytes,
                        file_name=file_name,
                        progress_callback=progress_callback,
                        force=force,
                    )
                )
            except DuplicateDocumentError as exc:
                existing = self.repository.get_by_hash(exc.document_hash)
                if existing:
                    results.append(
                        ProcessResult(
                            product=existing,
                            parsed=self.repository.get_parsed(existing.document.document_id),
                            cached=True,
                            duplicate=True,
                        )
                    )
            except Exception as exc:
                results.append(
                    ProcessResult(
                        product=None,
                        parsed=None,
                        cached=False,
                        error=f"{file_name}: {exc}",
                    )
                )
        return results

    def _is_current_schema(self, product: CanonicalProduct) -> bool:
        try:
            existing = tuple(int(part) for part in str(product.schema_version).split("."))
            current = tuple(int(part) for part in str(self.settings.schema_version).split("."))
        except ValueError:
            return False
        return existing >= current

    def _load_bytes(self, pdf: str | Path | bytes, file_name: str | None) -> tuple[bytes, str]:
        if isinstance(pdf, bytes):
            return pdf, file_name or "uploaded.pdf"
        path = Path(pdf)
        return path.read_bytes(), file_name or path.name

    def _resolve_document_id(self, file_name: str, document_hash: str) -> str:
        stem = sanitize_document_id(file_name)
        existing = self.repository.get_by_document_id(stem)
        if existing and existing.document.document_hash != document_hash:
            return f"{stem}_{document_hash[:8]}"
        return stem


def create_extraction_service(settings: Settings | None = None) -> ExtractionService:
    return ExtractionService(settings=settings)
