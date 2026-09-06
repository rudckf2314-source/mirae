from __future__ import annotations

import re
import hashlib
from datetime import date
from typing import Iterable

from schemas.product import CanonicalProduct, EvidenceItem
from schemas.risk_extraction import UnknownRiskTemplateDiagnostic
from schemas.product_schema import (
    Evidence,
    EvidenceExtractionMethod,
    ExtractionIssue,
    ExtractionStatus,
    Fee,
    FinancialMetric,
    MasterFeederRelation,
    Product,
    ProductClass,
    ProductExtraction,
    QualityControl,
    RateCondition,
    RiskRating,
    SalesCharge,
    SourceDocument,
    Narrative,
    PerformanceRecord,
)


class SchemaMapper:
    """Deterministic mapper from extraction-canonical JSON to DB-contract schema.

    This layer intentionally does not call an LLM. It only maps facts that already
    survived extraction/verification into ProductExtraction v0.1.
    """

    def map(self, canonical: CanonicalProduct) -> ProductExtraction:
        evidence_lookup = {e.chunk_id: e for e in canonical.evidence}
        evidence: list[Evidence] = []
        evidence_ids_seen: set[str] = set()

        def add_evidence(
            refs: Iterable[str],
            field_path: str,
            method: EvidenceExtractionMethod,
            *,
            row_index: int | None = None,
            column_name: str | None = None,
            raw_cell_text: str | None = None,
        ) -> list[str]:
            ids: list[str] = []
            for ref in refs:
                item = evidence_lookup.get(ref)
                if not item:
                    continue
                ev_id = self._evidence_id(item, field_path)
                ids.append(ev_id)
                if ev_id in evidence_ids_seen:
                    continue
                evidence_ids_seen.add(ev_id)
                evidence.append(
                    Evidence(
                        evidence_id=ev_id,
                        field_path=field_path,
                        page=max(1, item.page_start),
                        section=item.section_type,
                        source_text=item.source_text,
                        table_markdown=item.table_markdown,
                        source_hash=item.source_hash,
                        row_index=row_index,
                        column_name=column_name,
                        raw_cell_text=raw_cell_text,
                        extraction_method=method,
                        confidence=None,
                    )
                )
            return ids

        doc = canonical.document
        product = canonical.product
        source_document = SourceDocument(
            document_id=doc.document_id,
            filename=doc.file_name,
            as_of_date=self._date(doc.as_of_date),
            effective_date=self._date(doc.effective_date),
            revision_date=None,
            page_count=doc.page_count,
            file_hash=doc.document_hash,
        )

        classification = [self._compact(x) for x in product.classification]
        if not product.name:
            raise ValueError("Schema mapping failed: product.official_name source value is missing.")

        product_key = self._product_key(
            product.fund_code, product.name, product.manager
        )
        std_product = Product(
            product_key=product_key,
            official_name=product.name,
            kofia_fund_code=product.fund_code,
            manager_name=product.manager,
            legal_form=self._legal_form(product.classification),
            asset_type=product.asset_type,
            is_open_end=self._tag_bool(classification, ("개방형",), ("폐쇄형",)),
            is_additional=self._tag_bool(classification, ("추가형",), ("단위형",)),
            is_class_type=self._tag_bool(classification, ("종류형",), ()),
            is_master_feeder=self._tag_bool(classification, ("모자형",), ()),
            is_convertible=self._tag_bool(classification, ("전환형",), ()),
            is_high_complexity_product=None,
            inception_date=None,
        )

        risk_ratings: list[RiskRating] = []
        if product.risk.grade is not None:
            path = "risk_ratings[0].grade"
            risk_ratings.append(
                RiskRating(
                    grade=product.risk.grade,
                    label=product.risk.label,
                    method=None,
                    as_of_date=self._date(doc.as_of_date),
                    evidence_ids=add_evidence(product.risk.evidence_refs, path, EvidenceExtractionMethod.TEXT),
                )
            )

        supported_class_names = self._supported_class_names(canonical, evidence_lookup)
        classes: list[ProductClass] = []
        class_key_by_name: dict[str, str] = {}
        class_key_by_identity: dict[str, str] = {}
        for idx, item in enumerate(canonical.classes):
            if not item.class_name or item.class_name not in supported_class_names:
                continue
            key = self._class_key(item.class_name, idx, product_key)
            # Compacted names can collide (e.g. "종류C-F-0" vs "종류C-F----0").
            existing_keys = {row.class_key for row in classes}
            if key in existing_keys:
                suffix = 2
                while f"{key}__{suffix}" in existing_keys:
                    suffix += 1
                key = f"{key}__{suffix}"
            class_key_by_name[item.class_name] = key
            class_key_by_identity[self._class_key(item.class_name, 0).lower()] = key
            inception_date, inception_refs = self._class_inception_date(
                item.class_name, canonical, evidence_lookup
            )
            class_refs = list(dict.fromkeys([*item.evidence_refs, *inception_refs]))
            classes.append(
                ProductClass(
                    class_key=key,
                    class_name=item.class_name,
                    inception_date=self._date(item.inception_date) or inception_date,
                    sales_charge_type=self._sales_charge_type(item.class_name),
                    channel=self._channel(item.class_name),
                    pension_type=self._pension_type(item.class_name),
                    is_online=self._is_online(item.class_name),
                    evidence_ids=add_evidence(
                        class_refs,
                        f"classes[{idx}]",
                        EvidenceExtractionMethod.TABLE,
                    ),
                )
            )

        sales_charges: list[SalesCharge] = []
        fees: list[Fee] = []
        for idx, item in enumerate(canonical.fees):
            class_key = class_key_by_name.get(item.class_name or "")
            if not class_key and item.class_name:
                class_key = class_key_by_identity.get(
                    self._class_key(item.class_name, 0).lower()
                )
            if not class_key:
                continue
            if item.fee_type == "sales_fee":
                condition = item.condition or item.note
                rate_condition = self._rate_condition(condition)
                kwargs = {"rate": item.rate, "rate_min": None, "rate_max": None}
                if rate_condition == RateCondition.MAX:
                    kwargs = {"rate": None, "rate_min": None, "rate_max": item.rate}
                elif rate_condition == RateCondition.MIN:
                    kwargs = {"rate": None, "rate_min": item.rate, "rate_max": None}
                sales_charges.append(
                    SalesCharge(
                        class_key=class_key,
                        charge_type="sales_fee",
                        rate_condition=rate_condition,
                        condition_text=condition,
                        evidence_ids=add_evidence(
                            item.evidence_refs,
                            f"sales_charges[{len(sales_charges)}]",
                            EvidenceExtractionMethod.TABLE,
                            row_index=item.row_index,
                            column_name=item.column_name,
                            raw_cell_text=item.raw_cell_text,
                        ),
                        **kwargs,
                    )
                )
            else:
                if item.rate is not None and item.rate < 0:
                    continue
                fees.append(
                    Fee(
                        class_key=class_key,
                        fee_type=item.fee_type or "unknown",
                        rate=item.rate,
                        as_of_date=self._date(item.as_of_date),
                        evidence_ids=add_evidence(
                            item.evidence_refs,
                            f"fees[{len(fees)}]",
                            EvidenceExtractionMethod.TABLE,
                            row_index=item.row_index,
                            column_name=item.column_name,
                            raw_cell_text=item.raw_cell_text,
                        ),
                    )
                )

        performance: list[PerformanceRecord] = []
        for item in canonical.performance:
            class_key = class_key_by_name.get(item.class_name or "") if item.class_name else None
            if not class_key and item.class_name:
                class_key = class_key_by_identity.get(
                    self._class_key(item.class_name, 0).lower()
                )
            if item.class_name and not class_key:
                continue
            performance.append(
                PerformanceRecord(
                    class_key=class_key,
                    metric=item.metric_type or item.kind or "unknown",
                    period=item.period or "UNKNOWN",
                    return_type=item.kind,
                    value=item.return_rate,
                    unit="PERCENT" if (item.unit or "%") == "%" else (item.unit or "PERCENT"),
                    as_of_date=self._date(item.as_of_date),
                    period_start=self._date(item.period_start),
                    period_end=self._date(item.period_end),
                    evidence_ids=add_evidence(
                        item.evidence_refs,
                        f"performance[{len(performance)}]",
                        EvidenceExtractionMethod.TABLE,
                        row_index=item.row_index,
                        column_name=item.column_name,
                        raw_cell_text=item.raw_cell_text,
                    ),
                )
            )

        financial_metrics: list[FinancialMetric] = []
        for idx, item in enumerate(canonical.aum):
            financial_metrics.append(
                FinancialMetric(
                    metric_type="AUM",
                    raw_value=item.value,
                    raw_unit=item.unit or item.currency,
                    normalized_value_krw=item.value if (item.currency or "KRW") == "KRW" else None,
                    as_of_date=self._date(item.as_of_date),
                    evidence_ids=add_evidence(
                        item.evidence_refs,
                        f"financial_metrics[{idx}]",
                        EvidenceExtractionMethod.TEXT,
                    ),
                )
            )

        narratives: list[Narrative] = []
        if product.investment_objective.text:
            narratives.append(
                Narrative(
                    narrative_type="INVESTMENT_OBJECTIVE",
                    text=product.investment_objective.text,
                    evidence_ids=add_evidence(
                        product.investment_objective.evidence_refs,
                        "narratives.INVESTMENT_OBJECTIVE",
                        EvidenceExtractionMethod.LLM,
                    ),
                )
            )
        if product.investment_strategy.text:
            narratives.append(
                Narrative(
                    narrative_type="INVESTMENT_STRATEGY",
                    text=product.investment_strategy.text,
                    evidence_ids=add_evidence(
                        product.investment_strategy.evidence_refs,
                        "narratives.INVESTMENT_STRATEGY",
                        EvidenceExtractionMethod.LLM,
                    ),
                )
            )
        for idx, risk in enumerate(product.investment_risks):
            if not risk.name and not risk.description:
                continue
            text = risk.name or "투자위험"
            if risk.description:
                text = f"{text}: {risk.description}"
            narratives.append(
                Narrative(
                    narrative_type="INVESTMENT_RISK",
                    text=text,
                    evidence_ids=add_evidence(
                        risk.evidence_refs,
                        f"narratives.INVESTMENT_RISK[{idx}]",
                        EvidenceExtractionMethod.LLM,
                    ),
                )
            )

        master_feeder_relations = self._master_feeder_relations(
            canonical, add_evidence
        )
        hedging_policies = []
        field_status = self._field_status(
            canonical,
            risk_ratings,
            classes,
            sales_charges,
            fees,
            master_feeder_relations,
            hedging_policies,
            performance,
            narratives,
        )
        issues = self._issues(canonical)

        result = ProductExtraction(
            source_document=source_document,
            product=std_product,
            investment_profile=None,
            risk_ratings=risk_ratings,
            classes=classes,
            sales_charges=sales_charges,
            fees=fees,
            class_transition_rules=[],
            fund_conversion_rules=[],
            master_feeder_relations=master_feeder_relations,
            hedging_policies=hedging_policies,
            performance=performance,
            capital_flows=[],
            financial_metrics=financial_metrics,
            liquidity_rules=[],
            narratives=narratives,
            evidence=evidence,
            field_status=field_status,
            extraction_issues=issues,
            quality_control=QualityControl(
                verification_status=canonical.extraction.verification.status,
                verification_fail_count=canonical.extraction.verification.fail_count,
                contradicted_fields=[
                    item.field_path
                    for item in canonical.extraction.verification.items
                    if item.status == "FAIL" or item.verdict == "CONTRADICTED"
                ],
                review_required=(
                    canonical.extraction.status != "success"
                    or canonical.extraction.verification.status != "PASS"
                ),
            ),
        )
        return ProductExtraction.model_validate(result.model_dump())

    def _supported_class_names(
        self,
        canonical: CanonicalProduct,
        evidence_lookup: dict[str, EvidenceItem],
    ) -> set[str]:
        deterministic_names: set[str] = set()
        deterministic_keys: set[str] = set()
        for item in [*canonical.fees, *canonical.performance]:
            name = item.class_name
            if not name or not any(ref in evidence_lookup for ref in item.evidence_refs):
                continue
            deterministic_names.add(self._compact(name))
            deterministic_keys.add(self._class_key(name, 0).lower())

        supported: set[str] = set()
        for item in canonical.classes:
            name = item.class_name
            if not name:
                continue
            has_direct_evidence = any(ref in evidence_lookup for ref in item.evidence_refs)
            confirmed_by_table = (
                self._compact(name) in deterministic_names
                or self._class_key(name, 0).lower() in deterministic_keys
            )
            if has_direct_evidence or confirmed_by_table:
                supported.add(name)
        return supported

    def _class_inception_date(
        self,
        class_name: str,
        canonical: CanonicalProduct,
        evidence_lookup: dict[str, EvidenceItem],
    ) -> tuple[date | None, list[str]]:
        target_key = self._class_key(class_name, 0).lower()
        refs = list(
            dict.fromkeys(
                ref
                for item in canonical.performance
                if item.class_name
                and self._class_key(item.class_name, 0).lower() == target_key
                for ref in item.evidence_refs
            )
        )
        for ref in refs:
            evidence = evidence_lookup.get(ref)
            if not evidence or "최초설정일" not in evidence.source_text:
                continue
            for line in evidence.source_text.splitlines():
                if "|" not in line:
                    continue
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) < 2:
                    continue
                if self._class_key(cells[0], 0).lower() != target_key:
                    continue
                inception = self._date(cells[1])
                if inception:
                    return inception, [ref]
        return None, []

    def _master_feeder_relations(
        self,
        canonical: CanonicalProduct,
        add_evidence,
    ) -> list[MasterFeederRelation]:
        """Resolve parent-fund relations from already-verified narrative/evidence.

        This mapper stays deterministic: it never invents a fund name or ratio.
        It scans objective/strategy plus their evidence because many prospectuses
        mention the master fund in either semantic section.
        """
        if not any("모자형" in item for item in canonical.product.classification):
            return []

        evidence_lookup = {item.chunk_id: item for item in canonical.evidence}
        refs = list(dict.fromkeys([
            *canonical.product.investment_strategy.evidence_refs,
            *canonical.product.investment_objective.evidence_refs,
        ]))
        # A relation is a sourced fact.  Narrative text may have been rewritten by
        # an LLM, so names are extracted from evidence spans only.
        source_parts = [
            evidence_lookup[ref].source_text for ref in refs if ref in evidence_lookup
        ]
        source = re.sub(r"\s+", " ", " ".join(source_parts)).strip()
        if not source:
            return []

        # Broad but source-grounded name extraction. The matched value itself must
        # contain '모투자신탁'; surrounding prose is trimmed conservatively.
        raw_names: list[str] = []
        # Keep patterns linear-time. Nested quantifiers over long prospectus text
        # previously caused catastrophic backtracking and hung SchemaMapper.
        patterns = (
            r"[‘'\"]([^‘’'\"]{2,120}모투자신탁(?:\([^)]{0,40}\))?)[’'\"]",
            r"모투자신탁(?:인|으로|은|는|:)\s*[‘'\"]?([^‘’'\".,;\n]{2,120}모투자신탁(?:\([^)]{0,40}\))?)",
            r"([가-힣A-Za-z0-9·ㆍ()\-]{1,40}(?:\s+[가-힣A-Za-z0-9·ㆍ()\-]{1,40}){0,10}\s*모투자신탁(?:\([^)]{0,40}\))?)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, source):
                name = self._clean_master_name(match.group(1))
                for expanded in self._split_master_names(name):
                    if expanded and self._valid_master_name(expanded) and expanded not in raw_names:
                        raw_names.append(expanded)

        if not raw_names:
            return []

        # Ratios are attached only when their scope is unambiguous. If multiple
        # master funds share one aggregate ratio, keep per-fund ratios null rather
        # than copying the aggregate percentage to every parent.
        ratio_mentions = self._ratio_mentions(source)
        single_name = len(raw_names) == 1
        relations: list[MasterFeederRelation] = []
        for index, name in enumerate(raw_names):
            minimum = maximum = None
            local = self._nearest_ratio_for_name(source, name) if single_name else None
            if local is not None:
                minimum, maximum = local
            elif single_name and len(ratio_mentions) == 1:
                minimum, maximum = ratio_mentions[0]

            matching_refs = [
                ref for ref in refs
                if ref in evidence_lookup
                and self._compact(name) in self._compact(evidence_lookup[ref].source_text)
            ]
            if not matching_refs:
                continue
            evidence_ids = add_evidence(
                matching_refs,
                f"master_feeder_relations[{index}]",
                EvidenceExtractionMethod.REGEX,
            )
            relations.append(
                MasterFeederRelation(
                    master_product_name=name,
                    minimum_investment_ratio=minimum,
                    maximum_investment_ratio=maximum,
                    ratio_unit="PERCENT",
                    evidence_ids=evidence_ids,
                )
            )
        return relations

    @staticmethod
    def _clean_master_name(value: str) -> str:
        value = re.sub(r"\s+", " ", value or "").strip(" ‘’'\".,;:：")
        # Remove common lead-in prose accidentally captured before a fund name.
        value = re.sub(r"^(?:이\s*투자신탁은\s*|이\s*투자신탁의\s*|당해\s*투자신탁의\s*|주로\s*|다음\s*)", "", value)
        return value.strip()

    @classmethod
    def _split_master_names(cls, value: str) -> list[str]:
        if not value:
            return []
        parts = re.split(r"\s+(?:및|또는|과|와)\s+", value)
        if len(parts) <= 1:
            return [value]
        cleaned = [cls._clean_master_name(part) for part in parts]
        # Split only when each side independently names a master fund.
        if all("모투자신탁" in re.sub(r"\s+", "", part) for part in cleaned):
            return cleaned
        return [value]

    @staticmethod
    def _valid_master_name(name: str) -> bool:
        compact = re.sub(r"\s+", "", name or "")
        if "모투자신탁" not in compact:
            return False
        if len(compact) < 6 or len(compact) > 90:
            return False
        # Reject generic phrases lacking a product-identifying prefix or phrases
        # where a percentage condition was accidentally captured as the name.
        if compact in {"모투자신탁", "해당모투자신탁", "관련모투자신탁"}:
            return False
        if compact.startswith(("이상을모투자신탁", "이하를모투자신탁", "초과를모투자신탁", "미만을모투자신탁")):
            return False
        if re.match(r"^\d", compact):
            return False
        # OfficialNameGate: reject prose accidentally ending in 모투자신탁.
        # These are semantic descriptions, not product identifiers.
        prose_markers = (
            "투자목적", "목적으로", "투자대상", "주된투자", "주로투자",
            "수익률을추적", "추구하는", "투자하는", "이상을투자",
            "이하를투자", "국내주식에", "채권에투자", "것을목적",
        )
        if any(marker in compact for marker in prose_markers):
            return False
        prefix = compact.split("모투자신탁", 1)[0]
        if len(prefix) < 2 or prefix.endswith(("하는", "되는", "위한", "인")):
            return False
        return True

    @staticmethod
    def _ratio_mentions(source: str) -> list[tuple[float | None, float | None]]:
        out: list[tuple[float | None, float | None]] = []
        # Parse ranges first and mask them from the scalar pass, otherwise
        # ``50%~80%`` is incorrectly seen as two independent exact ratios.
        ranges: list[tuple[int, int]] = []
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%\s*[~～\-]\s*(\d+(?:\.\d+)?)\s*%", source):
            item = (float(match.group(1)), float(match.group(2)))
            if item not in out:
                out.append(item)
            ranges.append(match.span())
        masked = "".join(
            " " if any(start <= index < end for start, end in ranges) else char
            for index, char in enumerate(source)
        )
        bound_ranges: list[tuple[int, int]] = []
        for match in re.finditer(r"(최소|최대)\s*(\d+(?:\.\d+)?)\s*%", masked):
            value = float(match.group(2))
            item = (value, None) if match.group(1) == "최소" else (None, value)
            if item not in out:
                out.append(item)
            bound_ranges.append(match.span())
        scalar_source = "".join(
            " " if any(start <= index < end for start, end in bound_ranges) else char
            for index, char in enumerate(masked)
        )
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%\s*(이상|이하|초과|미만)?", scalar_source):
            value = float(match.group(1))
            cond = match.group(2) or ""
            if cond in {"이상", "초과"}:
                item = (value, None)
            elif cond in {"이하", "미만"}:
                item = (None, value)
            else:
                item = (value, value)
            if item not in out:
                out.append(item)
        return out

    @classmethod
    def _nearest_ratio_for_name(cls, source: str, name: str) -> tuple[float | None, float | None] | None:
        pos = source.find(name)
        if pos < 0:
            return None
        window = source[max(0, pos - 100): min(len(source), pos + len(name) + 120)]
        # Require an explicit investment relation in the local window.
        if not any(token in window for token in ("투자", "편입", "자산")):
            return None
        ratios = cls._ratio_mentions(window)
        return ratios[0] if len(ratios) == 1 else None

    def _field_status(
        self,
        canonical,
        risk_ratings,
        classes,
        sales_charges,
        fees,
        master_feeder_relations,
        hedging_policies,
        performance,
        narratives,
    ):
        owner = {x.field: x.status for x in canonical.extraction.ownership}
        narrative_types = {n.narrative_type for n in narratives}
        absent_msgs = [
            *(item.reason or "" for item in canonical.extraction.ownership if item.field == "performance"),
            *canonical.extraction.info,
            *canonical.extraction.audit,
        ]
        perf_absent = any("SOURCE_ABSENT" in (msg or "") for msg in absent_msgs)

        def status(field: str, found: bool) -> ExtractionStatus:
            if found:
                return ExtractionStatus.FOUND
            if field == "performance" and perf_absent:
                return ExtractionStatus.NOT_APPLICABLE
            raw = (owner.get(field) or "").upper()
            if raw in ExtractionStatus.__members__:
                return ExtractionStatus[raw]
            if raw == "REJECTED":
                return ExtractionStatus.NOT_FOUND
            return ExtractionStatus.NOT_FOUND

        return {
            "source_document.as_of_date": ExtractionStatus.FOUND if canonical.document.as_of_date else ExtractionStatus.NOT_FOUND,
            "source_document.effective_date": ExtractionStatus.FOUND if canonical.document.effective_date else ExtractionStatus.NOT_FOUND,
            "product.official_name": ExtractionStatus.FOUND if canonical.product.name else ExtractionStatus.NOT_FOUND,
            "product.kofia_fund_code": ExtractionStatus.FOUND if canonical.product.fund_code else ExtractionStatus.NOT_FOUND,
            "product.manager_name": ExtractionStatus.FOUND if canonical.product.manager else ExtractionStatus.NOT_FOUND,
            "product.asset_type": ExtractionStatus.FOUND if canonical.product.asset_type else ExtractionStatus.NOT_FOUND,
            "risk_ratings": status("risk", bool(risk_ratings)),
            "classes": status("classes", bool(classes)),
            "sales_charges": status("fees", bool(sales_charges)),
            "fees": status("fees", bool(fees)),
            "master_feeder_relations": (
                ExtractionStatus.FOUND
                if master_feeder_relations
                else (
                    ExtractionStatus.NOT_FOUND
                    if canonical.product.classification
                    and any("모자형" in item for item in canonical.product.classification)
                    else ExtractionStatus.NOT_APPLICABLE
                )
            ),
            "hedging_policies": (
                ExtractionStatus.FOUND
                if hedging_policies
                else ExtractionStatus.NOT_FOUND
            ),
            "performance": status("performance", bool(performance)),
            "narratives.INVESTMENT_OBJECTIVE": status("investment_objective", "INVESTMENT_OBJECTIVE" in narrative_types),
            "narratives.INVESTMENT_STRATEGY": status("investment_strategy", "INVESTMENT_STRATEGY" in narrative_types),
            "narratives.INVESTMENT_RISK": status("investment_risks", "INVESTMENT_RISK" in narrative_types),
        }

    @staticmethod
    def _issues(canonical: CanonicalProduct) -> list[ExtractionIssue]:
        issues: list[ExtractionIssue] = []
        final = {
            "classes": bool(canonical.classes),
            "fees": bool(canonical.fees),
            "performance": bool(canonical.performance),
            "investment_objective": bool((canonical.product.investment_objective.text or "").strip()),
            "investment_strategy": bool((canonical.product.investment_strategy.text or "").strip()),
            "investment_risks": bool(canonical.product.investment_risks),
        }
        for field in canonical.extraction.missing_fields:
            if field in final and final[field]:
                continue
            issues.append(
                ExtractionIssue(
                    field_path=field,
                    issue_type=ExtractionStatus.NOT_FOUND,
                    severity="WARNING",
                    message=f"Final canonical value is missing: {field}",
                )
            )
        for warning in canonical.extraction.warnings:
            if SchemaMapper._warning_contradicted_by_final_state(warning, final):
                continue
            field = SchemaMapper._warning_field(warning)
            issues.append(
                ExtractionIssue(
                    field_path=field,
                    issue_type=ExtractionStatus.AMBIGUOUS,
                    severity="WARNING",
                    message=warning,
                )
            )
        for info in canonical.extraction.info:
            field = SchemaMapper._warning_field(info)
            issue_type = (
                ExtractionStatus.NOT_APPLICABLE
                if "SOURCE_ABSENT" in info
                else ExtractionStatus.NOT_FOUND
            )
            issues.append(
                ExtractionIssue(
                    field_path=field,
                    issue_type=issue_type,
                    severity="INFO",
                    message=info,
                )
            )
        for raw_diagnostic in canonical.extraction.risk_diagnostics:
            diagnostic = UnknownRiskTemplateDiagnostic.model_validate(raw_diagnostic)
            issues.append(
                ExtractionIssue(
                    field_path="narratives.INVESTMENT_RISK",
                    issue_type=ExtractionStatus.PARSE_FAILED,
                    severity="WARNING",
                    message=(
                        f"{diagnostic.code}: structure={diagnostic.structure_type.value}; "
                        f"stage={diagnostic.failure_stage}; {diagnostic.reason}"
                    ),
                    page=diagnostic.page,
                )
            )
        return issues

    @staticmethod
    def _warning_contradicted_by_final_state(warning: str, final: dict[str, bool]) -> bool:
        compact = re.sub(r"\s+", "", (warning or "").lower())
        if final["classes"] and any(x in compact for x in (
            "classes배열을비워", "클래스이름을확인할수없", "클래스명을찾을수없",
            "noclassnamesfound", "nosalesclassnamesfound", "classinformationnotfound",
        )):
            return True
        if final["fees"] and final["performance"] and any(x in compact for x in (
            "수수료및성과표", "feeandperformancetable", "fees/performance",
        )) and any(x in compact for x in ("추출하지않", "불명확", "notextract", "ambiguous")):
            return True
        if final["investment_risks"] and any(x in compact for x in (
            "투자위험설명을찾을수없", "투자위험정보를찾을수없", "noinvestmentriskdetailsfound",
            "investmentrisksnotfound",
        )):
            # Keep precise per-risk description warnings; remove only generic whole-field claims.
            return "risk_description_missing" not in compact
        if final["investment_objective"] and "investment_objective" in compact and "notfound" in compact:
            return True
        if final["investment_strategy"] and "investment_strategy" in compact and "notfound" in compact:
            return True
        return False

    @staticmethod
    def _warning_field(warning: str) -> str:
        low = warning.lower()
        for token, path in (
            ("investment_strategy", "narratives.INVESTMENT_STRATEGY"),
            ("investment_objective", "narratives.INVESTMENT_OBJECTIVE"),
            ("investment_risk", "narratives.INVESTMENT_RISK"),
            ("risk_description", "narratives.INVESTMENT_RISK"),
            ("class", "classes"),
            ("fee", "fees"),
            ("performance", "performance"),
        ):
            if token in low:
                return path
        return "extraction"

    @staticmethod
    def _evidence_id(item: EvidenceItem, field_path: str) -> str:
        suffix = re.sub(r"[^A-Za-z0-9]+", "_", field_path).strip("_")[:48]
        return f"{item.chunk_id}__{suffix}"

    @staticmethod
    def _date(value: str | date | None) -> date | None:
        if value is None or isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    @staticmethod
    def _compact(value: str) -> str:
        return re.sub(r"\s+", "", value or "")

    @staticmethod
    def _legal_form(tags: list[str]) -> str | None:
        for tag in tags:
            if "투자신탁" in tag:
                return "투자신탁"
            if "투자회사" in tag:
                return "투자회사"
        return None

    @staticmethod
    def _tag_bool(tags: list[str], positives: tuple[str, ...], negatives: tuple[str, ...]) -> bool | None:
        if any(any(p in tag for p in positives) for tag in tags):
            return True
        if negatives and any(any(n in tag for n in negatives) for tag in tags):
            return False
        return None

    @staticmethod
    def _class_key(name: str, index: int, product_key: str | None = None) -> str:
        match = re.search(r"\(([^()]{1,24})\)\s*$", name or "")
        if match:
            local_key = match.group(1).strip()
        else:
            compact = re.sub(r"[^A-Za-z0-9가-힣]+", "_", name or "").strip("_")
            local_key = compact[:40] or f"CLASS_{index + 1:03d}"
        return f"{product_key}:{local_key}" if product_key else local_key

    @classmethod
    def _product_key(
        cls, fund_code: str | None, official_name: str, manager: str | None
    ) -> str:
        code = re.sub(r"[^A-Za-z0-9]", "", fund_code or "").upper()
        if code:
            return f"KOFIA:{code}"
        identity = f"{cls._compact(manager or '')}|{cls._compact(official_name)}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return f"NAMEHASH:{digest}"

    @staticmethod
    def _sales_charge_type(name: str) -> str | None:
        if "선취" in name:
            return "FRONT_END"
        if "후취" in name:
            return "BACK_END"
        if "미징구" in name:
            return "NONE"
        return None

    @staticmethod
    def _channel(name: str) -> str | None:
        if "온라인" in name:
            return "ONLINE"
        if "오프라인" in name:
            return "OFFLINE"
        return None

    @staticmethod
    def _pension_type(name: str) -> str | None:
        if "퇴직연금" in name:
            return "RETIREMENT_PENSION"
        if "개인연금" in name or "연금저축" in name:
            return "PERSONAL_PENSION"
        return None

    @staticmethod
    def _is_online(name: str) -> bool | None:
        if "온라인" in name:
            return True
        if "오프라인" in name:
            return False
        return None

    @staticmethod
    def _rate_condition(condition: str | None) -> RateCondition:
        compact = re.sub(r"\s+", "", condition or "")
        if any(x in compact for x in ("이내", "이하", "최대")):
            return RateCondition.MAX
        if any(x in compact for x in ("이상", "최소")):
            return RateCondition.MIN
        if condition:
            return RateCondition.EXACT
        return RateCondition.UNKNOWN
