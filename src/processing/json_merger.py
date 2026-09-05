from processing.class_candidates import normalize_class_name
from schemas.chunk import Chunk
from schemas.document import ParsedDocument
from schemas.extraction import LLMExtractionResult
from schemas.product import (
    CanonicalProduct,
    DocumentMeta,
    EvidenceItem,
    ExtractionMeta,
    FeeItem,
    PerformanceItem,
    ProductClass,
    ProductInfo,
)


class JsonMerger:
    def merge(
        self,
        parsed: ParsedDocument,
        chunks: list[Chunk],
        llm_result: LLMExtractionResult,
    ) -> CanonicalProduct:
        llm_result = self._sanitize_result(llm_result)
        classes = self._ensure_classes(llm_result.classes, llm_result.fees, llm_result.performance)
        llm_result.classes = classes
        llm_result.missing_fields = self._prune_missing_fields(llm_result)
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        used_refs = self._collect_refs(llm_result)
        evidence = []
        for chunk_id, chunk in chunk_map.items():
            if chunk_id not in used_refs:
                continue
            evidence.append(
                EvidenceItem(
                    chunk_id=chunk.chunk_id,
                    document_id=parsed.document_id,
                    file_name=parsed.file_name,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section_type=chunk.section_type.value,
                    # Evidence is an audit record, not a preview. Truncating a
                    # long reconstructed table can remove the exact row that a
                    # later deterministic extractor references.
                    source_text=chunk.text,
                    table_id=chunk.table_id,
                )
            )

        product = llm_result.product or ProductInfo()
        return CanonicalProduct(
            document=DocumentMeta(
                document_id=parsed.document_id,
                document_hash=parsed.document_hash,
                file_name=parsed.file_name,
                as_of_date=llm_result.as_of_date,
                effective_date=llm_result.effective_date,
                page_count=parsed.page_count,
            ),
            product=product,
            classes=classes,
            fees=self._drop_empty_fees(llm_result.fees),
            performance=self._drop_empty_performance(llm_result.performance),
            aum=llm_result.aum,
            evidence=evidence,
            extraction=ExtractionMeta(
                status="success",
                missing_fields=[],
                warnings=list(dict.fromkeys(llm_result.warnings)),
                ownership=llm_result.ownership,
                candidate_outcomes=llm_result.candidate_outcomes,
                run_report={"nodes": llm_result.run_metrics},
                risk_diagnostics=llm_result.risk_diagnostics,
            ),
        )

    def merge_llm_results(self, results: list[LLMExtractionResult]) -> LLMExtractionResult:
        if not results:
            return LLMExtractionResult()
        merged = results[0].model_copy(deep=True)
        conflict_warnings: list[str] = []
        for extra in results[1:]:
            merged.as_of_date = merged.as_of_date or extra.as_of_date
            merged.effective_date = merged.effective_date or extra.effective_date
            merged.product = self._merge_product(merged.product, extra.product)
            merged.classes, class_warn = self._merge_by_keys(
                merged.classes, extra.classes, ("class_name",), "classes"
            )
            merged.fees, fee_warn = self._merge_by_keys(
                merged.fees, extra.fees, ("class_name", "fee_type", "as_of_date"), "fees"
            )
            merged.performance, perf_warn = self._merge_by_keys(
                merged.performance,
                extra.performance,
                ("subject", "class_name", "metric_type", "period", "as_of_date"),
                "performance",
            )
            merged.aum, aum_warn = self._merge_by_keys(
                merged.aum, extra.aum, ("as_of_date", "unit"), "aum"
            )
            conflict_warnings.extend(class_warn + fee_warn + perf_warn + aum_warn)
            merged.missing_fields = sorted(set(merged.missing_fields + extra.missing_fields))
            merged.warnings = sorted(set(merged.warnings + extra.warnings + conflict_warnings))
            existing_outcomes = {(item.field, item.owner) for item in merged.ownership}
            merged.ownership.extend(
                item
                for item in extra.ownership
                if (item.field, item.owner) not in existing_outcomes
            )
            existing_candidates = {
                (item.field, item.owner, item.candidate_id)
                for item in merged.candidate_outcomes
            }
            merged.candidate_outcomes.extend(
                item
                for item in extra.candidate_outcomes
                if (item.field, item.owner, item.candidate_id) not in existing_candidates
            )
            merged.run_metrics.extend(extra.run_metrics)
            known_diagnostics = {
                (item.document_id, item.page, item.failure_stage, item.reason)
                for item in merged.risk_diagnostics
            }
            merged.risk_diagnostics.extend(
                item
                for item in extra.risk_diagnostics
                if (item.document_id, item.page, item.failure_stage, item.reason)
                not in known_diagnostics
            )
        return self._sanitize_result(merged)

    def _sanitize_result(self, result: LLMExtractionResult) -> LLMExtractionResult:
        result.product.investment_risks = [
            item
            for item in result.product.investment_risks
            if (item.name and item.name.strip()) or (item.description and item.description.strip())
        ]
        result.classes = self._drop_empty_classes(result.classes)
        result.fees = self._drop_empty_fees(result.fees)
        result.performance = self._drop_empty_performance(result.performance)
        result.classes = self._ensure_classes(result.classes, result.fees, result.performance)
        result.missing_fields = self._prune_missing_fields(result)
        return result

    def _ensure_classes(
        self,
        classes: list[ProductClass],
        fees: list[FeeItem],
        performance: list[PerformanceItem],
    ) -> list[ProductClass]:
        existing: dict[str, ProductClass] = {}
        derived: list[ProductClass] = []
        for item in classes:
            name = normalize_class_name(item.class_name)
            if not name or name in existing:
                continue
            item.class_name = name
            existing[name] = item
            derived.append(item)
        for item in list(fees) + list(performance):
            name = normalize_class_name(getattr(item, "class_name", None))
            if not name or name in existing or name in {"비교지수", "수익률변동성"}:
                continue
            cls = ProductClass(class_name=name, evidence_refs=list(item.evidence_refs))
            existing[name] = cls
            derived.append(cls)
        return derived

    def _prune_missing_fields(self, result: LLMExtractionResult) -> list[str]:
        aliases = {
            "as_of_date": bool(result.as_of_date),
            "effective_date": bool(result.effective_date),
            "fees": bool(result.fees),
            "performance": bool(result.performance),
            "classes": bool(result.classes),
            "aum": bool(result.aum),
            "investment_objective": bool(result.product.investment_objective.text),
            "investment_objective.text": bool(result.product.investment_objective.text),
            "product.investment_objective": bool(result.product.investment_objective.text),
            "investment_strategy": bool(result.product.investment_strategy.text),
            "investment_strategy.text": bool(result.product.investment_strategy.text),
            "product.investment_strategy": bool(result.product.investment_strategy.text),
            "investment_risks": bool(result.product.investment_risks),
        }
        kept = []
        for item in result.missing_fields:
            if aliases.get(item):
                continue
            kept.append(item)
        return kept

    def _drop_empty_classes(self, items: list[ProductClass]) -> list[ProductClass]:
        return [item for item in items if item.class_name]

    def _drop_empty_fees(self, items: list[FeeItem]) -> list[FeeItem]:
        kept = []
        for item in items:
            if item.rate is None and not item.condition and not item.note and not item.class_name:
                continue
            if item.class_name:
                item.class_name = normalize_class_name(item.class_name)
            kept.append(item)
        return kept

    def _drop_empty_performance(self, items: list[PerformanceItem]) -> list[PerformanceItem]:
        return [item for item in items if item.return_rate is not None]

    def _merge_product(self, base: ProductInfo, extra: ProductInfo) -> ProductInfo:
        data = base.model_dump()
        other = extra.model_dump()
        for key in ("name", "manager", "asset_type", "fund_code"):
            data[key] = data.get(key) or other.get(key)
        data["classification"] = list(
            dict.fromkeys((data.get("classification") or []) + (other.get("classification") or []))
        )
        risk = data["risk"]
        extra_risk = other["risk"]
        risk["grade"] = risk.get("grade") if risk.get("grade") is not None else extra_risk.get("grade")
        risk["label"] = risk.get("label") or extra_risk.get("label")
        risk["evidence_refs"] = list(
            dict.fromkeys((risk.get("evidence_refs") or []) + (extra_risk.get("evidence_refs") or []))
        )
        for field_name in ("investment_objective", "investment_strategy"):
            current = data[field_name]
            incoming = other[field_name]
            current["text"] = current.get("text") or incoming.get("text")
            current["evidence_refs"] = list(
                dict.fromkeys((current.get("evidence_refs") or []) + (incoming.get("evidence_refs") or []))
            )
        existing_names = {item.get("name") for item in data["investment_risks"] if item.get("name")}
        for item in other["investment_risks"]:
            if item.get("name") and item.get("name") not in existing_names:
                data["investment_risks"].append(item)
        return ProductInfo.model_validate(data)

    def _merge_by_keys(
        self,
        base: list,
        extra: list,
        keys: tuple[str, ...],
        label: str,
    ) -> tuple[list, list[str]]:
        warnings: list[str] = []
        index: dict[tuple, object] = {}
        ordered: list = []
        for item in list(base) + list(extra):
            payload = item.model_dump() if hasattr(item, "model_dump") else item
            identity = tuple(payload.get(key) for key in keys)
            existing = index.get(identity)
            if existing is None:
                index[identity] = item
                ordered.append(item)
                continue
            old = existing.model_dump() if hasattr(existing, "model_dump") else existing
            if self._significant_values(old) != self._significant_values(payload):
                warnings.append(f"duplicate {label} with conflicting values: {identity}")
        return ordered, warnings

    def _significant_values(self, payload: dict) -> tuple:
        skip = {"evidence_refs", "note"}
        return tuple((k, payload.get(k)) for k in sorted(payload) if k not in skip)

    def _collect_refs(self, result: LLMExtractionResult) -> set[str]:
        tmp = CanonicalProduct(
            document=DocumentMeta(
                document_id="tmp",
                document_hash="tmp",
                file_name="tmp.pdf",
            ),
            product=result.product,
            classes=result.classes,
            fees=result.fees,
            performance=result.performance,
            aum=result.aum,
        )
        return set(tmp.all_evidence_refs())
