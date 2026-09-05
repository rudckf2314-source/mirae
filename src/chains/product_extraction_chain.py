from __future__ import annotations

import json
import operator
import re
import time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from exceptions import LlmError, LlmRateLimitError, LlmTimeoutError, MalformedLlmResponseError
from processing.json_merger import JsonMerger
from processing.metadata_extractor import apply_metadata_facts
from processing.narrative_extractor import (
    apply_narrative_facts,
    recover_objective_from_chunks,
    recover_strategy_from_chunks,
)
from processing.risk_row_extractor import RiskCandidate, collect_table_risk_candidates
from processing.progress import ProgressCallback, emit
from processing.table_extractor import extract_table_facts
from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable, ParsedDocument
from schemas.extraction import LLMExtractionResult, RiskClassificationResult
from schemas.product import CanonicalProduct, DocumentMeta, InvestmentRiskItem, NodeRunMetric
from workflows.checkpoint_store import NodeCheckpointStore

from .prompts import (
    DESCRIPTION_PROMPT,
    FEE_PROMPT,
    METADATA_PROMPT,
    OBJECTIVE_RECOVERY_PROMPT,
    PERFORMANCE_PROMPT,
    RISK_RECOVERY_PROMPT,
    SEMANTIC_REVIEW_PROMPT,
    STRATEGY_RESELECT_PROMPT,
)


class PrimaryExtractionState(TypedDict, total=False):
    chunks: list[Chunk]
    tables: list[DetectedTable]
    document_hash: str
    parsed: ParsedDocument
    progress_callback: ProgressCallback | None
    table_result: LLMExtractionResult
    metadata_result: LLMExtractionResult
    narrative_result: LLMExtractionResult
    merged_result: LLMExtractionResult
    run_metrics: Annotated[list[NodeRunMetric], operator.add]


class ReviewState(TypedDict, total=False):
    result: LLMExtractionResult
    review_chunks: list[Chunk]
    objective_locked: bool
    strategy_locked: bool
    risk_candidates: list[RiskCandidate]


class ProductExtractionChain:
    """Facade that routes section/table chunks to specialized LangChain runnables."""

    def __init__(
        self,
        llm: BaseChatModel,
        max_retries: int = 2,
        merger: JsonMerger | None = None,
        use_structured_output: bool = False,
        enable_semantic_review: bool = True,
        fail_fast_on_llm_error: bool = False,
        checkpoint_store: NodeCheckpointStore | None = None,
    ):
        self.llm = llm
        self.max_retries = max_retries
        self.merger = merger or JsonMerger()
        self.use_structured_output = use_structured_output
        self.enable_semantic_review = enable_semantic_review
        self.fail_fast_on_llm_error = fail_fast_on_llm_error
        self.checkpoint_store = checkpoint_store
        self.metadata_chain = self._build_json_chain(METADATA_PROMPT)
        self.fee_chain = self._build_json_chain(FEE_PROMPT)
        self.performance_chain = self._build_json_chain(PERFORMANCE_PROMPT)
        self.description_chain = self._build_json_chain(DESCRIPTION_PROMPT)
        self.semantic_review_chain = self._build_json_chain(SEMANTIC_REVIEW_PROMPT)
        self.strategy_reselect_chain = self._build_json_chain(STRATEGY_RESELECT_PROMPT)
        self.objective_recovery_chain = self._build_json_chain(OBJECTIVE_RECOVERY_PROMPT)
        self.risk_recovery_chain = self._build_json_chain(RISK_RECOVERY_PROMPT)
        self.primary_graph = self._build_primary_graph().compile()
        self.review_graph = self._build_review_graph().compile()

    def invoke(self, input_data: dict) -> LLMExtractionResult:
        return self.extract(
            input_data["chunks"],
            tables=input_data.get("tables") or [],
            progress_callback=input_data.get("progress_callback"),
            parsed=input_data.get("parsed"),
        )

    def extract(
        self,
        chunks: list[Chunk],
        tables: list[DetectedTable] | None = None,
        progress_callback: ProgressCallback | None = None,
        document_hash: str | None = None,
        parsed: ParsedDocument | None = None,
    ) -> LLMExtractionResult:
        primary = self.primary_graph.invoke({
            "chunks": chunks,
            "tables": tables or [],
            "progress_callback": progress_callback,
            "document_hash": document_hash or "",
            "parsed": parsed,
        })
        results = [
            primary["table_result"],
            primary["metadata_result"],
            primary["narrative_result"],
        ]
        if not results:
            raise MalformedLlmResponseError("LLM 서브체인이 결과를 반환하지 않았습니다.")
        merged = primary["merged_result"]
        review_chunks = self._contextual_description_chunks(chunks)
        if review_chunks:
            try:
                risk_candidates = collect_table_risk_candidates(chunks, tables or [])
                reviewed_state = self.review_graph.invoke({
                    "result": merged,
                    "review_chunks": review_chunks,
                    "objective_locked": False,
                    "strategy_locked": False,
                    "risk_candidates": risk_candidates,
                })
                merged = reviewed_state["result"]
            except (MalformedLlmResponseError, LlmTimeoutError, LlmError) as exc:
                if self.fail_fast_on_llm_error:
                    raise
                merged.warnings.append(f"SemanticReviewChain 실패: {exc}")
        if all(item.missing_fields and not item.product.name and not item.fees and not item.performance for item in results):
            if not merged.product.name and not merged.fees and not merged.performance:
                raise MalformedLlmResponseError("모든 Extraction Chain 결과가 비었습니다.")
        return merged

    def _build_primary_graph(self) -> StateGraph:
        graph = StateGraph(PrimaryExtractionState)
        graph.add_node("table_facts", self._primary_table_facts)
        graph.add_node("metadata", self._primary_metadata)
        graph.add_node("narrative", self._primary_narrative)
        graph.add_node("merge_primary", self._merge_primary)
        # These nodes write distinct state keys and therefore run in parallel.
        graph.add_edge(START, "table_facts")
        graph.add_edge(START, "metadata")
        graph.add_edge(START, "narrative")
        graph.add_edge("table_facts", "merge_primary")
        graph.add_edge("metadata", "merge_primary")
        graph.add_edge("narrative", "merge_primary")
        graph.add_edge("merge_primary", END)
        return graph

    def _build_review_graph(self) -> StateGraph:
        graph = StateGraph(ReviewState)
        graph.add_node("deterministic_objective", self._deterministic_objective)
        graph.add_node("semantic_review", self._review_semantics)
        graph.add_node("strategy_reselection", self._review_strategy)
        graph.add_node("objective_recovery", self._recover_objective)
        graph.add_node("risk_recovery", self._recover_risk)
        graph.add_edge(START, "deterministic_objective")
        graph.add_conditional_edges(
            "deterministic_objective",
            lambda state: "semantic_review" if (
                self.enable_semantic_review
                and not (state.get("objective_locked") and state.get("strategy_locked"))
            ) else "objective_recovery",
            ["semantic_review", "objective_recovery"],
        )
        graph.add_conditional_edges(
            "semantic_review",
            lambda state: "strategy_reselection" if (
                not state.get("strategy_locked") and self._needs_strategy_reselection(state["result"])
            ) else "objective_recovery",
            ["strategy_reselection", "objective_recovery"],
        )
        graph.add_edge("strategy_reselection", "objective_recovery")
        graph.add_edge("objective_recovery", "risk_recovery")
        graph.add_edge("risk_recovery", END)
        return graph

    def _deterministic_objective(self, state: ReviewState) -> dict:
        started = time.perf_counter()
        result = state["result"].model_copy(deep=True)
        candidate = recover_objective_from_chunks(state["review_chunks"])
        strategy = recover_strategy_from_chunks(state["review_chunks"])
        if strategy is not None:
            result.product.investment_strategy = strategy
        if candidate is None:
            result.run_metrics.append(self._run_metric("deterministic_objective", started))
            return {
                "result": result,
                "objective_locked": False,
                "strategy_locked": strategy is not None,
            }
        result.product.investment_objective = candidate
        result.warnings.append(
            "OBJECTIVE_RECOVERY_TRACE: deterministic sentence candidate accepted and source-anchored."
        )
        result.run_metrics.append(self._run_metric("deterministic_objective", started))
        if strategy is not None and self.enable_semantic_review:
            result.run_metrics.append(NodeRunMetric(node="semantic_review", executed=False))
        return {
            "result": result,
            "objective_locked": True,
            "strategy_locked": strategy is not None,
        }

    def _review_semantics(self, state: ReviewState) -> dict:
        started = time.perf_counter()
        current = state["result"].model_copy(deep=True)
        locked = current.product.investment_objective.model_copy(deep=True)
        locked_strategy = current.product.investment_strategy.model_copy(deep=True)
        reviewed = self._invoke_semantic_review(current, state["review_chunks"])
        current = self._apply_semantic_review(current, reviewed)
        if state.get("objective_locked"):
            current.product.investment_objective = locked
        if state.get("strategy_locked"):
            current.product.investment_strategy = locked_strategy
        current.run_metrics.append(self._run_metric("semantic_review", started, llm_calls=1))
        return {"result": current}

    def _review_strategy(self, state: ReviewState) -> dict:
        started = time.perf_counter()
        result = state["result"].model_copy(deep=True)
        replacement = self._invoke_strategy_reselection(result, state["review_chunks"])
        result = self._apply_strategy_reselection(result, replacement)
        result.run_metrics.append(self._run_metric("strategy_reselection", started, llm_calls=1))
        return {"result": result}

    def _recover_objective(self, state: ReviewState) -> dict:
        started = time.perf_counter()
        result = state["result"].model_copy(deep=True)
        if (result.product.investment_objective.text or "").strip() or not self._has_objective_signal(state["review_chunks"]):
            result.run_metrics.append(self._run_metric("objective_recovery", started, executed=False))
            return {"result": result}
        result.warnings.append(
            "OBJECTIVE_RECOVERY_TRACE: no deterministic sentence candidate survived; invoking semantic reader."
        )
        recovered = self._invoke_recovery(
            self.objective_recovery_chain, state["review_chunks"], "ObjectiveRecovery"
        )
        if (recovered.product.investment_objective.text or "").strip():
            result.product.investment_objective = recovered.product.investment_objective
        result.run_metrics.append(self._run_metric("objective_recovery", started, llm_calls=1))
        return {"result": result}

    def _recover_risk(self, state: ReviewState) -> dict:
        started = time.perf_counter()
        result = state["result"].model_copy(deep=True)
        candidates = state.get("risk_candidates") or []
        if result.product.investment_risks or not candidates:
            result.run_metrics.append(self._run_metric("risk_recovery", started, executed=False))
            return {"result": result}
        decision = self._invoke_risk_classifier(candidates, state["review_chunks"])
        accepted = set(decision.accepted_candidate_ids)
        result.product.investment_risks = [
            InvestmentRiskItem(
                name=candidate.name,
                description=candidate.description,
                evidence_refs=list(candidate.evidence_refs),
            )
            for candidate in candidates
            if candidate.candidate_id in accepted
        ]
        result.run_metrics.append(self._run_metric("risk_recovery", started, llm_calls=1))
        return {"result": result}

    def _primary_table_facts(self, state: PrimaryExtractionState) -> dict:
        started = time.perf_counter()
        document_hash = state.get("document_hash") or ""
        document_id = state["chunks"][0].document_id if state["chunks"] else None
        cached = self.checkpoint_store.load_model(
            document_hash, "table_facts", "v2", LLMExtractionResult,
            document_id=document_id,
        ) if self.checkpoint_store and document_hash else None
        if cached is not None:
            return {
                "table_result": cached,
                "run_metrics": [self._run_metric("table_facts", started, cache_hit=True)],
            }
        result = extract_table_facts(state["tables"], state["chunks"])
        if self.checkpoint_store and document_hash:
            self.checkpoint_store.save_model(
                document_hash, "table_facts", "v2", result, document_id=document_id
            )
        return {"table_result": result, "run_metrics": [self._run_metric("table_facts", started)]}

    def _primary_metadata(self, state: PrimaryExtractionState) -> dict:
        started = time.perf_counter()
        deterministic = self._deterministic_primary_result(
            state["chunks"], state.get("tables") or [], include_metadata=True
        )
        if deterministic.product.name and deterministic.product.manager:
            return {
                "metadata_result": deterministic,
                "run_metrics": [self._run_metric("metadata", started, executed=False)],
            }
        jobs = self._route(state["chunks"], state["tables"])
        job = next((item for item in jobs if item[0] == "ProductMetadataChain"), None)
        result = self._run_primary_job(job, state.get("progress_callback"), 1, 2)
        return {
            "metadata_result": result,
            "run_metrics": [self._run_metric("metadata", started, llm_calls=1 if job else 0, executed=bool(job))],
        }

    def _primary_narrative(self, state: PrimaryExtractionState) -> dict:
        started = time.perf_counter()
        deterministic = self._deterministic_primary_result(
            state["chunks"],
            state.get("tables") or [],
            include_narrative=True,
            parsed=state.get("parsed"),
        )
        objective = (deterministic.product.investment_objective.text or "").strip()
        strategy = (deterministic.product.investment_strategy.text or "").strip()
        risks_complete = bool(deterministic.product.investment_risks) or not self._has_risk_signal(
            state["chunks"]
        )
        if objective and strategy and risks_complete:
            return {
                "narrative_result": deterministic,
                "run_metrics": [self._run_metric("narrative", started, executed=False)],
            }
        jobs = self._route(state["chunks"], state["tables"])
        job = next((item for item in jobs if item[0] == "InvestmentDescriptionChain"), None)
        result = self._run_primary_job(job, state.get("progress_callback"), 2, 2)
        # LLM narrative output never owns Risk facts. Preserve only deterministic
        # source-row items and discard any unexpected model-provided risks.
        result.product.investment_risks = list(deterministic.product.investment_risks)
        result.risk_diagnostics = list(deterministic.risk_diagnostics)
        if objective:
            result.product.investment_objective = deterministic.product.investment_objective
        if strategy:
            result.product.investment_strategy = deterministic.product.investment_strategy
        return {
            "narrative_result": result,
            "run_metrics": [self._run_metric("narrative", started, llm_calls=1 if job else 0, executed=bool(job))],
        }

    @staticmethod
    def _deterministic_primary_result(
        chunks: list[Chunk],
        tables: list[DetectedTable],
        *,
        include_metadata: bool = False,
        include_narrative: bool = False,
        parsed: ParsedDocument | None = None,
    ) -> LLMExtractionResult:
        """Build a source-anchored preflight result without invoking an LLM."""
        document_id = chunks[0].document_id if chunks else "preflight"
        canonical = CanonicalProduct(document=DocumentMeta(
            document_id=document_id,
            document_hash="preflight",
            file_name=f"{document_id}.pdf",
        ))
        if include_metadata:
            canonical = apply_metadata_facts(canonical, chunks)
        if include_narrative:
            canonical = apply_narrative_facts(
                canonical, chunks, tables, parsed=parsed
            )
        return LLMExtractionResult(
            as_of_date=canonical.document.as_of_date,
            effective_date=canonical.document.effective_date,
            product=canonical.product,
            classes=canonical.classes,
            ownership=canonical.extraction.ownership,
            candidate_outcomes=canonical.extraction.candidate_outcomes,
            risk_diagnostics=canonical.extraction.risk_diagnostics,
        )

    def _run_primary_job(
        self,
        job: tuple[str, Runnable, list[Chunk]] | None,
        progress_callback: ProgressCallback | None,
        batch: int,
        total: int,
    ) -> LLMExtractionResult:
        if job is None:
            return LLMExtractionResult()
        name, chain, selected = job
        emit(
            progress_callback,
            "extracting",
            f"Parallel LLM Extraction ({batch}/{total}): {name}",
            status="started",
            batch=batch,
            total=total,
        )
        try:
            return self._invoke_once(chain, selected, name)
        except (MalformedLlmResponseError, LlmTimeoutError, LlmError) as exc:
            if self.fail_fast_on_llm_error:
                raise
            return LLMExtractionResult(warnings=[f"{name} 실패: {exc}"], missing_fields=[])

    def _merge_primary(self, state: PrimaryExtractionState) -> dict:
        # Fixed order makes conflict resolution reproducible regardless of
        # which parallel branch completes first.
        merged = self.merger.merge_llm_results([
            state["table_result"],
            state["metadata_result"],
            state["narrative_result"],
        ])
        merged.run_metrics = list(state.get("run_metrics") or [])
        return {"merged_result": merged}

    @staticmethod
    def _run_metric(
        node: str,
        started: float,
        *,
        cache_hit: bool = False,
        llm_calls: int = 0,
        executed: bool = True,
    ) -> NodeRunMetric:
        return NodeRunMetric(
            node=node,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            cache_hit=cache_hit,
            llm_calls=llm_calls,
            executed=executed,
        )

    def _route(
        self,
        chunks: list[Chunk],
        tables: list[DetectedTable],
        table_result: LLMExtractionResult | None = None,
    ) -> list[tuple[str, Runnable, list[Chunk]]]:
        jobs: list[tuple[str, Runnable, list[Chunk]]] = []
        table_result = table_result or LLMExtractionResult()
        meta_types = {
            SectionType.PRODUCT_INFO,
            SectionType.RISK_GRADE,
            SectionType.CLASS_INFO,
        }
        meta = [c for c in chunks if c.section_type in meta_types or c.page_start <= 2]
        if not meta:
            meta = [c for c in chunks if not c.table_id][:4]
        if meta:
            jobs.append(("ProductMetadataChain", self.metadata_chain, meta[:10]))

        desc_types = {
            SectionType.INVESTMENT_OBJECTIVE,
            SectionType.INVESTMENT_STRATEGY,
            SectionType.INVESTMENT_RISK,
        }
        desc = [c for c in chunks if c.section_type in desc_types and not c.table_id]
        if desc:
            jobs.append(("InvestmentDescriptionChain", self.description_chain, desc[:8]))

        if not jobs:
            jobs.append(("ProductMetadataChain", self.metadata_chain, chunks[:6]))
        return jobs


    def _contextual_description_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Narrative anchor 주변을 페이지 기준으로 다시 묶는다.

        section 라벨 자체보다 실제 페이지 문맥을 중요하게 본다. 같은/인접 페이지의
        OTHER/PERFORMANCE 표·주석도 포함해 metric definition과 실제 위험을 구분한다.
        """
        desc_types = {
            SectionType.INVESTMENT_OBJECTIVE,
            SectionType.INVESTMENT_STRATEGY,
            SectionType.INVESTMENT_RISK,
        }
        anchors = [
            c for c in chunks if c.section_type in desc_types and not c.table_id
        ]
        if not anchors:
            return []

        anchor_pages = {c.page_start for c in anchors}
        selected = []
        seen: set[str] = set()
        # 1) narrative anchor는 전부 우선 포함
        for chunk in sorted(anchors, key=lambda c: (c.page_start, c.chunk_id)):
            if chunk.chunk_id not in seen:
                selected.append(chunk)
                seen.add(chunk.chunk_id)
        # 2) 각 anchor의 같은/앞/뒤 페이지 문맥 추가
        context = [
            c for c in chunks
            if any(abs(c.page_start - page) <= 1 for page in anchor_pages)
            and c.chunk_id not in seen
        ]
        context.sort(key=lambda c: (c.page_start, 1 if c.table_id else 0, c.chunk_id))
        selected.extend(context)
        return selected[:16]

    def _invoke_semantic_review(
        self, current: LLMExtractionResult, chunks: list[Chunk]
    ) -> LLMExtractionResult:
        narrative = {
            "investment_objective": current.product.investment_objective.model_dump(),
            "investment_strategy": current.product.investment_strategy.model_dump(),
        }
        payload = {
            "current_narrative": json.dumps(narrative, ensure_ascii=False, indent=2),
            "chunk_ids": "\n".join(f"- {chunk.chunk_id}" for chunk in chunks),
            "chunks_text": self._format_chunks(chunks),
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = self.semantic_review_chain.invoke(payload)
                return self._coerce_result(raw)
            except Exception as exc:
                mapped = self._map_llm_error(exc)
                if isinstance(mapped, LlmRateLimitError):
                    raise mapped from exc
                last_error = mapped
                if isinstance(mapped, LlmTimeoutError) or attempt >= self.max_retries:
                    break
                time.sleep(0.8 * (attempt + 1))
        if isinstance(last_error, (LlmTimeoutError, LlmRateLimitError)):
            raise last_error
        raise MalformedLlmResponseError(f"Semantic review 응답 파싱 실패: {last_error}") from last_error

    def _apply_semantic_review(
        self, current: LLMExtractionResult, reviewed: LLMExtractionResult
    ) -> LLMExtractionResult:
        # 숫자/표/metadata는 절대 건드리지 않고 narrative만 교체한다.
        locked_risks = list(current.product.investment_risks)
        current.product.investment_objective = reviewed.product.investment_objective
        current.product.investment_strategy = reviewed.product.investment_strategy
        current.product.investment_risks = locked_risks
        for warning in reviewed.warnings:
            if warning not in current.warnings:
                current.warnings.append(warning)
        return current


    def _needs_strategy_reselection(self, result: LLMExtractionResult) -> bool:
        objective = (result.product.investment_objective.text or "").strip()
        strategy = (result.product.investment_strategy.text or "").strip()
        if not objective or not strategy:
            return False
        left = self._semantic_tokens(objective)
        right = self._semantic_tokens(strategy)
        if min(len(left), len(right)) < 6:
            return False
        containment = len(left & right) / min(len(left), len(right))
        compact = re.sub(r"\s+", "", strategy)
        objective_like = any(marker in compact for marker in ("목적으로합니다", "주목적으로합니다", "수익을추구", "자본이득및배당소득"))
        return containment >= 0.68 and objective_like

    @staticmethod
    def _semantic_tokens(text: str) -> set[str]:
        stop = {"이", "그", "및", "등", "것을", "합니다", "투자신탁", "투자신탁은", "집합투자기구", "집합투자기구는", "투자하여", "투자하고", "투자하는"}
        return {token for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", text.lower()) if token not in stop}

    def _invoke_strategy_reselection(self, current: LLMExtractionResult, chunks: list[Chunk]) -> LLMExtractionResult:
        payload = {
            "objective": current.product.investment_objective.text or "",
            "strategy": current.product.investment_strategy.text or "",
            "chunk_ids": "\n".join(f"- {chunk.chunk_id}" for chunk in chunks),
            "chunks_text": self._format_chunks(chunks),
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = self.strategy_reselect_chain.invoke(payload)
                return self._coerce_result(raw)
            except Exception as exc:
                mapped = self._map_llm_error(exc)
                if isinstance(mapped, LlmRateLimitError):
                    raise mapped from exc
                last_error = mapped
                if isinstance(mapped, LlmTimeoutError) or attempt >= self.max_retries:
                    break
                time.sleep(0.8 * (attempt + 1))
        if isinstance(last_error, (LlmTimeoutError, LlmRateLimitError)):
            raise last_error
        raise MalformedLlmResponseError(f"Strategy reselection 응답 파싱 실패: {last_error}") from last_error

    def _apply_strategy_reselection(self, current: LLMExtractionResult, replacement: LLMExtractionResult) -> LLMExtractionResult:
        candidate = replacement.product.investment_strategy
        if (candidate.text or "").strip():
            current.product.investment_strategy = candidate
            warning = "NARRATIVE_RESELECTED: investment_strategy replaced after objective/strategy overlap review."
            if warning not in current.warnings:
                current.warnings.append(warning)
        else:
            warning = "NARRATIVE_NEAR_DUPLICATE: no distinct investment_strategy candidate survived reselection."
            if warning not in current.warnings:
                current.warnings.append(warning)
        return current

    @staticmethod
    def _has_objective_signal(chunks: list[Chunk]) -> bool:
        compact = re.sub(r"\s+", "", "\n".join(c.text or "" for c in chunks))
        return any(marker in compact for marker in (
            "투자목적", "목적으로합니다", "수익을추구", "자본이득", "비교지수", "안정적인수익",
        ))

    @staticmethod
    def _has_risk_signal(chunks: list[Chunk]) -> bool:
        compact = re.sub(r"\s+", "", "\n".join(c.text or "" for c in chunks))
        if any(c.section_type == SectionType.INVESTMENT_RISK for c in chunks):
            return True
        return any(marker in compact for marker in (
            "투자위험", "위험요인", "원본손실위험", "원금손실위험", "가격변동위험",
            "신용위험", "유동성위험", "금리변동위험", "환율변동위험",
        ))

    def _invoke_recovery(self, chain: Runnable, chunks: list[Chunk], focus: str) -> LLMExtractionResult:
        payload = {
            "chunk_ids": "\n".join(f"- {chunk.chunk_id}" for chunk in chunks),
            "chunks_text": self._format_chunks(chunks),
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = chain.invoke(payload)
                return self._coerce_result(raw)
            except Exception as exc:
                mapped = self._map_llm_error(exc)
                if isinstance(mapped, LlmRateLimitError):
                    raise mapped from exc
                last_error = mapped
                if isinstance(mapped, LlmTimeoutError) or attempt >= self.max_retries:
                    break
                time.sleep(0.8 * (attempt + 1))
        if isinstance(last_error, (LlmTimeoutError, LlmRateLimitError)):
            raise last_error
        raise MalformedLlmResponseError(f"{focus} 응답 파싱 실패: {last_error}") from last_error

    def _invoke_risk_classifier(
        self,
        candidates: list[RiskCandidate],
        chunks: list[Chunk],
    ) -> RiskClassificationResult:
        allowed = {candidate.candidate_id for candidate in candidates}
        payload = {
            "risk_candidates": json.dumps(
                [
                    {
                        "candidate_id": candidate.candidate_id,
                        "name": candidate.name,
                        "description": candidate.description,
                        "evidence_refs": list(candidate.evidence_refs),
                    }
                    for candidate in candidates
                ],
                ensure_ascii=False,
                indent=2,
            ),
            "chunks_text": self._format_chunks(chunks),
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = self.risk_recovery_chain.invoke(payload)
                if hasattr(raw, "model_dump"):
                    raw = raw.model_dump()
                if isinstance(raw, str):
                    raw = json.loads(self._strip_fences(raw))
                decision = RiskClassificationResult.model_validate(raw)
                decision.accepted_candidate_ids = [
                    candidate_id
                    for candidate_id in decision.accepted_candidate_ids
                    if candidate_id in allowed
                ]
                return decision
            except Exception as exc:
                mapped = self._map_llm_error(exc)
                if isinstance(mapped, LlmRateLimitError):
                    raise mapped from exc
                last_error = mapped
                if isinstance(mapped, LlmTimeoutError) or attempt >= self.max_retries:
                    break
                time.sleep(0.8 * (attempt + 1))
        if isinstance(last_error, (LlmTimeoutError, LlmRateLimitError)):
            raise last_error
        raise MalformedLlmResponseError(
            f"Risk candidate classification 응답 파싱 실패: {last_error}"
        ) from last_error

    def _build_json_chain(self, prompt: ChatPromptTemplate) -> Runnable:
        return prompt | self.llm | JsonOutputParser()

    def _invoke_once(self, chain: Runnable, chunks: list[Chunk], focus: str) -> LLMExtractionResult:
        payload = {
            "chunk_ids": "\n".join(f"- {chunk.chunk_id}" for chunk in chunks),
            "chunks_text": self._format_chunks(chunks),
            "focus": focus,
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = chain.invoke(payload)
                return self._coerce_result(raw)
            except Exception as exc:
                mapped = self._map_llm_error(exc)
                if isinstance(mapped, LlmRateLimitError):
                    raise mapped from exc
                last_error = mapped
                if isinstance(mapped, LlmTimeoutError) or attempt >= self.max_retries:
                    break
                time.sleep(0.8 * (attempt + 1))
        if isinstance(last_error, (LlmTimeoutError, LlmRateLimitError)):
            raise last_error
        raise MalformedLlmResponseError(f"LLM 응답 파싱 실패: {last_error}") from last_error

    def _format_chunks(self, chunks: list[Chunk]) -> str:
        parts = []
        for chunk in chunks:
            parts.append(
                f"[CHUNK_ID: {chunk.chunk_id}]\n"
                f"(section={chunk.section_type.value}, pages={chunk.page_start}-{chunk.page_end}"
                f"{', table=' + chunk.table_id if chunk.table_id else ''})\n"
                f"{chunk.text}"
            )
        return "\n\n".join(parts)

    def _coerce_result(self, raw: object) -> LLMExtractionResult:
        if isinstance(raw, LLMExtractionResult):
            return raw
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        if isinstance(raw, str):
            raw = self._strip_fences(raw)
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise MalformedLlmResponseError("LLM 결과가 JSON 객체가 아닙니다.")
        product = raw.get("product")
        if product in (None, {}):
            raw["product"] = {}
        return LLMExtractionResult.model_validate(raw)

    def _strip_fences(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    def _map_llm_error(self, exc: Exception) -> Exception:
        text = str(exc).lower()
        if "timeout" in text or "timed out" in text:
            return LlmTimeoutError("HyperCLOVA X API timeout")
        if "429" in text or "rate limit" in text:
            return LlmRateLimitError("HyperCLOVA X API rate limit")
        return exc
