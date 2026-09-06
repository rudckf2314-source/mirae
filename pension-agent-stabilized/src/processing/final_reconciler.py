from __future__ import annotations

import re

from processing.post_processor import recompute_final_warnings
from processing.risk_heading_anchor import RiskHeadingAnchor
from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable
from schemas.product import CanonicalProduct
from validators.status import compute_final_status, partition_messages


class FinalReconciler:
    """Reconcile the final canonical state after verification.

    This stage does not invent or rewrite extracted facts. It only makes the final
    status/warnings/missing_fields consistent with the final owner state, source
    presence and verification result.
    """

    def __init__(self, risk_heading_anchor: RiskHeadingAnchor | None = None):
        self.risk_heading_anchor = risk_heading_anchor or RiskHeadingAnchor()

    def reconcile(
        self,
        product: CanonicalProduct,
        chunks: list[Chunk] | None = None,
        tables: list[DetectedTable] | None = None,
    ) -> CanonicalProduct:
        chunks = chunks or []
        tables = tables or []

        # DB-facing risk labels must be source-anchored, never LLM-synthesized.
        product = self.risk_heading_anchor.apply(product, chunks)
        product = self._sanitize_performance_facts(product, chunks, tables)

        warnings = recompute_final_warnings(product)
        warnings = self._drop_final_state_conflicts(product, warnings)

        presence = self._source_presence(chunks, tables)
        final_values = self._final_presence(product)
        # Rebuild missing_fields from the final canonical presence so intermediate
        # extraction leftovers cannot contradict populated fields.
        tracked = set(final_values)
        missing = [
            field
            for field in dict.fromkeys(product.extraction.missing_fields)
            if field not in tracked or not final_values[field]
        ]
        owner_map = {item.field: item for item in product.extraction.ownership}

        for field in (
            "investment_objective",
            "investment_strategy",
            "classes",
            "fees",
            "performance",
            "investment_risks",
        ):
            owner = owner_map.get(field)
            if not owner or owner.status == "VALID" or final_values[field]:
                continue
            if not presence[field]:
                # No source signal: retain owner audit trail, but do not claim the
                # document definitely contains a missing value.
                continue
            if field == "performance":
                from validators.source_absent import is_performance_source_absent

                if is_performance_source_absent(chunks, tables):
                    info_msg = (
                        "INFO: SOURCE_ABSENT: performance "
                        f"(owner={owner.owner}, status={owner.status})"
                    )
                    if info_msg not in warnings:
                        warnings.append(info_msg)
                    continue
            warning = (
                f"OWNER_UNRESOLVED: {field} source signal detected but final value is empty "
                f"(owner={owner.owner}, status={owner.status})."
            )
            if warning not in warnings:
                warnings.append(warning)
            if field not in missing:
                missing.append(field)

        # A field may be empty without a dedicated owner record. Presence-based
        # reconciliation still prevents a false final success.
        for field in ("investment_objective", "investment_strategy", "performance", "investment_risks"):
            if presence[field] and not final_values[field] and field not in missing:
                if field == "performance":
                    from validators.source_absent import is_performance_source_absent

                    if is_performance_source_absent(chunks, tables):
                        info_msg = "INFO: SOURCE_ABSENT: performance (source table present, values N/A)"
                        if info_msg not in warnings:
                            warnings.append(info_msg)
                        continue
                warning = f"FINAL_STATE_MISSING: {field} source signal detected but no canonical value survived."
                if warning not in warnings:
                    warnings.append(warning)
                missing.append(field)

        # Drop stale performance missing when SOURCE_ABSENT was confirmed.
        from validators.source_absent import is_performance_source_absent

        perf_absent = is_performance_source_absent(chunks, tables)
        if not final_values["performance"] and perf_absent:
            missing = [field for field in missing if field != "performance"]
            stale_perf_markers = (
                "수익률 표가 탐지되었으나",
                "table_gate_ambiguous: performance",
                "table_gate_rejected: performance",
                "OWNER_UNRESOLVED: performance",
                "FINAL_STATE_MISSING: performance",
            )
            warnings = [
                item
                for item in warnings
                if not any(marker in item for marker in stale_perf_markers)
                or item.startswith("INFO:")
            ]

        active, info, audit = partition_messages(
            [*product.extraction.audit, *product.extraction.info, *warnings]
        )
        product.extraction.warnings = list(dict.fromkeys(active))
        product.extraction.info = list(dict.fromkeys(info))
        product.extraction.audit = list(dict.fromkeys(audit))
        product.extraction.missing_fields = list(dict.fromkeys(missing))

        if product.extraction.missing_fields:
            product.extraction.validation.completeness_status = "WARNING"
        elif not product.extraction.warnings and product.extraction.validation.completeness_status == "WARNING":
            # Completeness may have been elevated only by now-INFO SOURCE_ABSENT noise.
            product.extraction.validation.completeness_status = "PASS"

        status = compute_final_status(
            product,
            product.extraction.validation,
            product.extraction.missing_fields,
            product.extraction.warnings,
        )
        verification_status = (product.extraction.verification.status or "PASS").upper()
        if verification_status == "FAIL":
            status = "failed"
        elif verification_status == "WARNING" and status == "success":
            status = "warning"
        product.extraction.status = status
        return product

    @staticmethod
    def _sanitize_performance_facts(
        product: CanonicalProduct,
        chunks: list[Chunk],
        tables: list[DetectedTable],
    ) -> CanonicalProduct:
        """Drop unsupported performance rows so quality gate can persist clean facts.

        - SOURCE_ABSENT prospectuses: clear invented performance entirely.
        - Otherwise: drop rows without evidence_refs or with verification FAIL.
        Verification items are rewritten so dropped rows cannot keep FAIL status.
        """
        from validators.source_absent import is_performance_source_absent

        report = product.extraction.verification
        fail_indices: set[int] = set()
        for item in report.items:
            path = item.field_path or ""
            if item.status != "FAIL" or not path.startswith("performance["):
                continue
            match = re.match(r"performance\[(\d+)\]", path)
            if match:
                fail_indices.add(int(match.group(1)))

        if is_performance_source_absent(chunks, tables):
            product.performance = []
            drop_all_perf = True
        else:
            drop_all_perf = False
            kept: list = []
            kept_old_indices: list[int] = []
            for index, row in enumerate(product.performance):
                if index in fail_indices:
                    continue
                if not row.evidence_refs:
                    continue
                kept.append(row)
                kept_old_indices.append(index)
            product.performance = kept

        new_items = []
        for item in report.items:
            path = item.field_path or ""
            if not path.startswith("performance["):
                new_items.append(item)
                continue
            if drop_all_perf:
                continue
            match = re.match(r"performance\[(\d+)\](.*)$", path)
            if not match:
                continue
            old_index = int(match.group(1))
            if old_index in fail_indices:
                continue
            if old_index not in kept_old_indices:
                continue
            new_index = kept_old_indices.index(old_index)
            suffix = match.group(2) or ""
            new_items.append(
                item.model_copy(update={"field_path": f"performance[{new_index}]{suffix}"})
            )

        fail_count = sum(1 for item in new_items if item.status == "FAIL")
        warning_count = sum(1 for item in new_items if item.status == "WARNING")
        pass_count = sum(1 for item in new_items if item.status == "PASS")
        skipped_count = sum(1 for item in new_items if item.status == "SKIPPED")
        if fail_count:
            status = "FAIL"
        elif warning_count:
            status = "WARNING"
        else:
            status = "PASS"
        product.extraction.verification = report.model_copy(
            update={
                "items": new_items,
                "checked": len(new_items),
                "pass_count": pass_count,
                "warning_count": warning_count,
                "fail_count": fail_count,
                "skipped_count": skipped_count,
                "status": status,
            }
        )
        return product

    def _drop_final_state_conflicts(self, product: CanonicalProduct, warnings: list[str]) -> list[str]:
        """Remove intermediate warnings contradicted by the final canonical state."""
        filled = self._final_presence(product)
        kept: list[str] = []
        for warning in warnings:
            compact = re.sub(r"\s+", "", warning.lower())

            if filled["classes"] and self._is_generic_class_missing(compact):
                continue
            if product.product.asset_type and (
                "assettypeinferredfromclassificationmaybeuncertain" in compact
                or "assettype" in compact and "uncertain" in compact
            ):
                continue
            if filled["investment_risks"] and (
                "investmentrisksnotfound" in compact
                or "noinvestmentriskdetailsfound" in compact
                or "noinvestmentriskinformationfound" in compact
                or "투자위험정보를찾을수없" in compact
                or "투자위험에대한구체적인정보를찾을수없" in compact
            ):
                continue
            if any((risk.description or "").strip() for risk in product.product.investment_risks) and (
                "투자위험에대한구체적인설명이문서에없" in compact
                or "noinvestmentriskdetailsfound" in compact
            ):
                continue
            if filled["investment_objective"] and (
                "investmentobjectivenotfound" in compact
                or "onlyinvestmentobjectiveextracted" in compact and filled["investment_strategy"]
                or "투자목적" in compact and ("없" in compact or "불완전" in compact)
            ):
                continue
            if filled["investment_strategy"] and (
                "investmentstrategynotfound" in compact
                or "onlyinvestmentobjectiveextracted" in compact
                or "투자전략" in compact and ("없" in compact or "불완전" in compact)
            ):
                continue
            kept.append(warning)
        return kept

    @staticmethod
    def _is_generic_class_missing(compact_warning: str) -> bool:
        markers = (
            "noclassnamesfound",
            "nosalesclassnamesfound",
            "noexplicitclassnames",
            "classnamesnotidentifiable",
            "classinformationnotfound",
            "classtablepresentbutclassnamesnotidentifiable",
            "클래스정보를표에서명확히추출할수없",
            "클래스정보를추출할수없",
            "클래스정보가없",
            "클래스명을찾을수없",
        )
        return any(marker in compact_warning for marker in markers)

    @staticmethod
    def _final_presence(product: CanonicalProduct) -> dict[str, bool]:
        return {
            "investment_objective": bool((product.product.investment_objective.text or "").strip()),
            "investment_strategy": bool((product.product.investment_strategy.text or "").strip()),
            "classes": bool(product.classes),
            "fees": bool(product.fees),
            "performance": bool(product.performance),
            "investment_risks": bool(product.product.investment_risks),
        }

    @staticmethod
    def _source_presence(chunks: list[Chunk], tables: list[DetectedTable]) -> dict[str, bool]:
        section_presence = {section: False for section in SectionType}
        for chunk in chunks:
            if (chunk.text or "").strip() or chunk.rows:
                section_presence[chunk.section_type] = True
        for table in tables:
            if table.headers or table.rows:
                section_presence[table.section_type] = True

        joined = "\n".join(chunk.text or "" for chunk in chunks)
        compact = re.sub(r"\s+", "", joined)
        risk_signal = section_presence[SectionType.INVESTMENT_RISK] or any(
            marker in compact
            for marker in (
                "투자위험",
                "주요투자위험",
                "위험요인",
                "원본손실위험",
                "가격변동위험",
                "신용위험",
                "유동성위험",
            )
        )
        objective_signal = section_presence[SectionType.INVESTMENT_OBJECTIVE] or "투자목적" in compact
        strategy_signal = section_presence[SectionType.INVESTMENT_STRATEGY] or "투자전략" in compact
        class_signal = section_presence[SectionType.CLASS_INFO] or "종류" in compact
        fee_signal = section_presence[SectionType.FEES] or any(x in compact for x in ("총보수", "판매수수료", "판매보수"))
        performance_signal = section_presence[SectionType.PERFORMANCE] or any(
            x in compact for x in ("최근1년", "최근2년", "설정일이후", "수익률변동성", "비교지수")
        )
        return {
            "investment_objective": objective_signal,
            "investment_strategy": strategy_signal,
            "classes": class_signal,
            "fees": fee_signal,
            "performance": performance_signal,
            "investment_risks": risk_signal,
        }
