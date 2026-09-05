from __future__ import annotations

import inspect
import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import RootModel

from exceptions import EmptyPdfError
from processing.progress import ProgressCallback, emit
from schemas.chunk import Chunk, SectionSpan
from schemas.document import ParsedDocument
from schemas.extraction import LLMExtractionResult
from schemas.product import CanonicalProduct, NodeRunMetric
from workflows.checkpoint_store import NodeCheckpointStore

PARSE_NODE_VERSION = "v4"
SECTION_NODE_VERSION = "v1"
CHUNK_NODE_VERSION = "v4"


class SectionCheckpoint(RootModel[list[SectionSpan]]):
    pass


class ChunkCheckpoint(RootModel[list[Chunk]]):
    pass


class ExtractionState(TypedDict, total=False):
    """One immutable-document run, keyed externally by document_hash."""

    pdf_bytes: bytes
    file_name: str
    document_id: str
    document_hash: str
    progress_callback: ProgressCallback | None
    parsed: ParsedDocument
    sections: list[Any]
    chunks: list[Chunk]
    llm_result: LLMExtractionResult
    canonical: CanonicalProduct
    node_metrics: Annotated[list[NodeRunMetric], operator.add]


class ExtractionGraph:
    """LangGraph workflow for extraction and deterministic finalization.

    Nodes return state deltas only. Persistence remains outside the graph so a
    failed run can never partially overwrite the canonical repository record.
    """

    def __init__(
        self,
        *,
        parser: Any,
        detector: Any,
        chunker: Any,
        chain: Any,
        merger: Any,
        post_processor: Any,
        validator: Any,
        verifier: Any,
        final_reconciler: Any,
        checkpoint_store: NodeCheckpointStore | None = None,
    ) -> None:
        self.parser = parser
        self.detector = detector
        self.chunker = chunker
        self.chain = chain
        self.merger = merger
        self.post_processor = post_processor
        self.validator = validator
        self.verifier = verifier
        self.final_reconciler = final_reconciler
        self.checkpoint_store = checkpoint_store
        self.compiled = self._build().compile()

    def _build(self) -> StateGraph:
        graph = StateGraph(ExtractionState)
        graph.add_node("parse", self._parse)
        graph.add_node("detect_sections", self._detect_sections)
        graph.add_node("chunk", self._chunk)
        graph.add_node("extract", self._extract)
        graph.add_node("merge", self._merge)
        graph.add_node("postprocess", self._postprocess)
        graph.add_node("validate", self._validate)
        graph.add_node("verify", self._verify)
        graph.add_node("reconcile", self._reconcile)
        graph.add_edge(START, "parse")
        graph.add_edge("parse", "detect_sections")
        graph.add_edge("detect_sections", "chunk")
        graph.add_edge("chunk", "extract")
        graph.add_edge("extract", "merge")
        graph.add_edge("merge", "postprocess")
        graph.add_edge("postprocess", "validate")
        graph.add_edge("validate", "verify")
        graph.add_edge("verify", "reconcile")
        graph.add_edge("reconcile", END)
        return graph

    def invoke(self, state: ExtractionState) -> ExtractionState:
        return self.compiled.invoke(state)

    @staticmethod
    def _callback(state: ExtractionState) -> ProgressCallback | None:
        return state.get("progress_callback")

    def _parse(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        cached = self.checkpoint_store.load_model(
            state["document_hash"], "parse", PARSE_NODE_VERSION, ParsedDocument,
            document_id=state["document_id"],
        ) if self.checkpoint_store else None
        if cached is not None:
            emit(callback, "parsing", "PDF Parsing checkpoint 재사용", cached=True)
            return {"parsed": cached, "node_metrics": [self._metric("parse", started, cache_hit=True)]}
        emit(callback, "parsing", "PDF Parsing", status="started")
        parsed = self.parser.parse(
            state["pdf_bytes"],
            file_name=state["file_name"],
            document_hash=state["document_hash"],
            document_id=state["document_id"],
        )
        emit(callback, "parsing", f"{parsed.page_count}페이지 텍스트 추출 완료", page_count=parsed.page_count)
        if self.checkpoint_store:
            self.checkpoint_store.save_model(
                state["document_hash"], "parse", PARSE_NODE_VERSION, parsed,
                document_id=state["document_id"],
            )
        return {"parsed": parsed, "node_metrics": [self._metric("parse", started)]}

    def _detect_sections(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        cached = self.checkpoint_store.load_model(
            state["document_hash"], "sections", SECTION_NODE_VERSION, SectionCheckpoint,
            document_id=state["document_id"],
        ) if self.checkpoint_store else None
        if cached is not None:
            emit(callback, "section_detection", "Section checkpoint 재사용", cached=True)
            return {"sections": cached.root, "node_metrics": [self._metric("detect_sections", started, cache_hit=True)]}
        emit(callback, "section_detection", "Section Detection", status="started")
        sections = self.detector.detect(state["parsed"])
        emit(callback, "section_detection", f"{len(sections)}개 섹션 구간 탐지")
        if self.checkpoint_store:
            self.checkpoint_store.save_model(
                state["document_hash"], "sections", SECTION_NODE_VERSION,
                SectionCheckpoint(sections), document_id=state["document_id"],
            )
        return {"sections": sections, "node_metrics": [self._metric("detect_sections", started)]}

    def _chunk(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        cached = self.checkpoint_store.load_model(
            state["document_hash"], "chunks", CHUNK_NODE_VERSION, ChunkCheckpoint,
            document_id=state["document_id"],
        ) if self.checkpoint_store else None
        if cached is not None:
            emit(callback, "chunking", "Chunk checkpoint 재사용", cached=True, chunk_count=len(cached.root))
            return {"chunks": cached.root, "node_metrics": [self._metric("chunk", started, cache_hit=True)]}
        emit(callback, "chunking", "Chunk 생성", status="started")
        parsed = state["parsed"]
        chunks = self.chunker.chunk(parsed, state["sections"], tables=parsed.tables)
        if not chunks:
            raise EmptyPdfError("Chunk를 생성할 텍스트가 없습니다.")
        emit(callback, "chunking", f"{len(chunks)}개 chunk 생성", chunk_count=len(chunks))
        if self.checkpoint_store:
            self.checkpoint_store.save_model(
                state["document_hash"], "chunks", CHUNK_NODE_VERSION,
                ChunkCheckpoint(chunks), document_id=state["document_id"],
            )
        return {"chunks": chunks, "node_metrics": [self._metric("chunk", started)]}

    def _extract(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        emit(callback, "extracting", "LLM Extraction", status="started")
        kwargs = {"tables": state["parsed"].tables, "progress_callback": callback}
        if "document_hash" in inspect.signature(self.chain.extract).parameters:
            kwargs["document_hash"] = state["document_hash"]
        if "parsed" in inspect.signature(self.chain.extract).parameters:
            kwargs["parsed"] = state["parsed"]
        result = self.chain.extract(state["chunks"], **kwargs)
        emit(callback, "extracting", "LLM Extraction 완료")
        return {"llm_result": result, "node_metrics": [self._metric("extract", started)]}

    def _merge(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        emit(callback, "merging", "JSON Merge", status="started")
        canonical = self.merger.merge(state["parsed"], state["chunks"], state["llm_result"])
        emit(callback, "merging", "Canonical JSON 병합 완료")
        return {"canonical": canonical, "node_metrics": [self._metric("merge", started)]}

    def _postprocess(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        emit(callback, "postprocess", "Post-processing", status="started")
        canonical = self.post_processor.process(
            state["canonical"], state["chunks"], tables=state["parsed"].tables
        )
        emit(callback, "postprocess", "Class/필드 후처리 완료")
        return {"canonical": canonical, "node_metrics": [self._metric("postprocess", started)]}

    def _validate(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        emit(callback, "validating", "Validation", status="started")
        canonical = self.validator.validate(
            state["canonical"], state["chunks"], tables=state["parsed"].tables
        )
        emit(callback, "validating", f"Validation {canonical.extraction.status}")
        return {"canonical": canonical, "node_metrics": [self._metric("validate", started)]}

    def _verify(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        emit(callback, "verifying", "Verification", status="started")
        canonical = self.verifier.verify(
            state["canonical"],
            state["chunks"],
            tables=state["parsed"].tables,
            llm=getattr(self.chain, "llm", None),
        )
        emit(callback, "verifying", f"Verification {canonical.extraction.verification.status}")
        return {"canonical": canonical, "node_metrics": [self._metric("verify", started)]}

    def _reconcile(self, state: ExtractionState) -> dict[str, Any]:
        started = time.perf_counter()
        callback = self._callback(state)
        emit(callback, "reconciling", "Final Reconciliation", status="started")
        canonical = self.final_reconciler.reconcile(
            state["canonical"], state["chunks"], tables=state["parsed"].tables
        )
        emit(callback, "reconciling", f"Final status {canonical.extraction.status}")
        return {"canonical": canonical, "node_metrics": [self._metric("reconcile", started)]}

    @staticmethod
    def _metric(node: str, started: float, *, cache_hit: bool = False) -> NodeRunMetric:
        return NodeRunMetric(
            node=node,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            cache_hit=cache_hit,
        )
