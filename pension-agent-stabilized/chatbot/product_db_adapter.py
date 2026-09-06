from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
import json
from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Any

from .irp_eligibility import evaluate_irp_eligibility
from .performance_audit import annotate_performance, selected_performance_audit
from .product_json_schema import ProductJsonDocument, RawPensionTypeContext
from .product_normalizer import ProductNormalizer
from .risk_policy import (
    RANKING_POLICY_DEFAULT,
    allowed_buckets,
    attach_ranking_breakdown,
    bucket_from_record,
    excluded_buckets,
    recommendation_sort_key,
    record_matches_tolerance,
)


class ProductDBAdapter(ABC):
    """상품 DB 저장방식(JSON/SQL/API)을 숨기는 공통 인터페이스."""

    @property
    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def search(self, question: str, limit: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError


class NullProductDBAdapter(ProductDBAdapter):
    """상품 DB가 아직 없을 때 사용하는 빈 어댑터."""

    @property
    def available(self) -> bool:
        return False

    def search(self, question: str, limit: int = 5) -> list[dict[str, Any]]:
        return []


@dataclass(frozen=True)
class ProductQuerySpec:
    """LLM 없이 해석하는 구조화 상품 검색 조건입니다."""

    irp_only: bool = False
    risk_grade_max: int | None = None
    risk_grade_min: int | None = None
    online_only: bool = False
    sort_by: str = "product_name"
    sort_order: str = "asc"
    performance_period: str | None = None
    performance_metric_type: str | None = None
    candidate_record_ids: tuple[str, ...] = ()
    limit: int = 5
    parse_issues: tuple[str, ...] = ()
    name_tokens: tuple[str, ...] = ()
    family_aliases: tuple[str, ...] = ()
    tenors: tuple[str, ...] = ()
    name_match_required: bool = False
    exact_product_names: tuple[str, ...] = ()
    risk_tolerance: str | None = None
    allowed_risk_buckets: tuple[str, ...] = ()
    excluded_risk_buckets: tuple[str, ...] = ()
    ranking_policy: tuple[str, ...] = ()


class JsonProductDBAdapter(ProductDBAdapter):
    """
    Postgres Standard JSON(또는 data/standard_json 폴백)을 상품 클래스 단위로 조회합니다.

    원본 스키마에서 연금 유형, 판매 채널, 총보수는 classes/fees에 있으므로,
    검색 결과 한 건은 상품 자체가 아니라 실제 가입 가능한 상품 클래스입니다.
    """

    TOTAL_FEE_TYPES = (
        "total_fee",
        "total_fee_and_expenses",
        "TOTAL",
        "TOTAL_FEE",
        "TOTAL_FEE_PRE_CONVERSION",
        "TOTAL_FEE_POST_CONVERSION",
    )
    MIN_RISK_GRADE = 1
    MAX_RISK_GRADE = 6
    MAX_PRODUCT_LIMIT = 10

    def __init__(
        self,
        path: str | Path,
        normalizer: ProductNormalizer | None = None,
        *,
        documents: list[tuple[str, dict[str, Any]]] | None = None,
        backend: str = "json",
        source_version: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.backend = backend
        self.source_version = source_version
        self.file_count = 0
        self.load_errors: list[str] = []
        self.normalizer = normalizer or ProductNormalizer.from_environment()
        self.validation_report: dict[str, Any] = {
            "files_checked": 0,
            "json_syntax_errors": 0,
            "schema_errors": 0,
            "normalizations": [],
        }
        if documents is not None:
            self.raw_records = self._records_from_documents(documents)
        else:
            self.raw_records = self._load_records()
        self.records = self._deduplicate_records(self.raw_records)
        self.last_search_trace: dict[str, Any] = {}

    @property
    def available(self) -> bool:
        return bool(self.records)

    @property
    def record_count(self) -> int:
        """중복 KOFIA 클래스 코드를 최신 기준일로 정리한 검색 가능 레코드 수입니다."""
        return len(self.records)

    @property
    def raw_record_count(self) -> int:
        """원본 JSON에서 읽은 중복 정리 전 상품 클래스 레코드 수입니다."""
        return len(self.raw_records)

    def search(self, question: str, limit: int = 5) -> list[dict[str, Any]]:
        spec = self.parse_query(question, limit)
        candidates = list(self.records)
        raw_count = len(candidates)
        identity = self._resolve_catalog_identity(question, candidates, spec)
        candidates = identity["rows"]
        after_identity = len(candidates)

        if spec.candidate_record_ids:
            allowed_ids = set(spec.candidate_record_ids)
            candidates = [record for record in candidates if str(record.get("record_id")) in allowed_ids]

        after_candidate_scope = len(candidates)
        if spec.irp_only:
            candidates = [record for record in candidates if self._is_irp_eligible(record)]
        after_account = len(candidates)

        if spec.risk_grade_max is not None:
            candidates = [
                record
                for record in candidates
                if record["risk_grade"] is not None
                and record["risk_grade"] <= spec.risk_grade_max
            ]
        if spec.risk_grade_min is not None:
            candidates = [
                record
                for record in candidates
                if record["risk_grade"] is not None
                and record["risk_grade"] >= spec.risk_grade_min
            ]
        if spec.risk_tolerance or spec.allowed_risk_buckets or spec.excluded_risk_buckets:
            filtered = []
            for record in candidates:
                if spec.risk_tolerance and not record_matches_tolerance(record, spec.risk_tolerance):
                    continue
                bucket = bucket_from_record(record)
                if spec.excluded_risk_buckets and bucket in spec.excluded_risk_buckets:
                    continue
                if spec.allowed_risk_buckets and bucket not in spec.allowed_risk_buckets:
                    continue
                filtered.append(record)
            candidates = filtered
        after_risk = len(candidates)

        if spec.online_only:
            candidates = [record for record in candidates if record["is_online"]]
        after_class = len(candidates)

        fee_null_dropped = 0
        if spec.sort_by == "total_fee":
            before_fee = len(candidates)
            candidates = [record for record in candidates if record["total_fee"] is not None]
            fee_null_dropped = before_fee - len(candidates)
            candidates.sort(
                key=lambda record: (record["total_fee"], record["product_name"], record["class_name"], record["record_id"]),
                reverse=spec.sort_order == "desc",
            )
        elif spec.sort_by == "performance":
            scored = []
            for record in candidates:
                annotated = annotate_performance(deepcopy(record))
                value = self._performance_value(annotated, spec.performance_metric_type or "fund_return", spec.performance_period or "1Y")
                if value is not None:
                    copy = annotated
                    audit = selected_performance_audit(
                        copy,
                        spec.performance_metric_type or "fund_return",
                        spec.performance_period or "1Y",
                    ) or {}
                    copy["selected_performance_value"] = value
                    copy["selected_performance_period"] = spec.performance_period or "1Y"
                    copy["selected_performance_metric_type"] = spec.performance_metric_type or "fund_return"
                    copy["selected_performance_unit"] = next(
                        (
                            item.get("unit")
                            for item in copy.get("performance") or []
                            if (item.get("metric_type") or item.get("metric") or "").casefold()
                            == (spec.performance_metric_type or "fund_return").casefold()
                            and str(item.get("period") or "").upper() == (spec.performance_period or "1Y").upper()
                        ),
                        None,
                    )
                    copy["selected_performance_audit"] = audit
                    copy["selected_performance_status"] = audit.get("status")
                    scored.append(copy)
            candidates = scored
            status_rank = {
                "VERIFIED": 0,
                "UNVERIFIED": 1,
                "UNIT_MISSING": 2,
                "SCALE_MISMATCH": 3,
                "SOURCE_CONFLICT": 4,
            }
            candidates.sort(
                key=lambda record: (
                    status_rank.get(str(record.get("selected_performance_status") or ""), 5),
                    -record["selected_performance_value"] if spec.sort_order == "desc" else record["selected_performance_value"],
                    record["product_name"],
                    record["class_name"],
                    record["record_id"],
                ),
            )
        else:
            candidates.sort(
                key=lambda record: (
                    record["product_name"],
                    record["class_name"],
                    record["record_id"],
                )
            )

        if identity.get("one_row_per_product") or "추천" in (question or ""):
            candidates = self._one_row_per_product(candidates, spec.sort_by)

        is_recommendation = "추천" in (question or "")
        if is_recommendation and spec.sort_by == "product_name":
            attach_ranking_breakdown(candidates, tolerance=spec.risk_tolerance, sort_by=spec.sort_by)
            candidates.sort(key=lambda record: recommendation_sort_key(record, spec.sort_by))
        else:
            attach_ranking_breakdown(candidates, tolerance=spec.risk_tolerance, sort_by=spec.sort_by)

        ranked = [deepcopy(record) for record in candidates[: spec.limit]]
        self.last_search_trace = {
            "product_query_spec": {
                "irp_only": spec.irp_only,
                "risk_grade_max": spec.risk_grade_max,
                "risk_tolerance": spec.risk_tolerance,
                "allowed_risk_buckets": list(spec.allowed_risk_buckets),
                "excluded_risk_buckets": list(spec.excluded_risk_buckets),
                "ranking_policy": list(spec.ranking_policy or RANKING_POLICY_DEFAULT),
                "sort_by": spec.sort_by,
                "sort_order": spec.sort_order,
                "limit": spec.limit,
                "name_tokens": list(spec.name_tokens),
                "family_aliases": list(spec.family_aliases),
                "tenors": list(spec.tenors),
                "name_match_required": spec.name_match_required,
                "candidate_record_ids": list(spec.candidate_record_ids),
            },
            "normalized_product_filter": identity.get("filter_label"),
            "db_rows_raw_count": raw_count,
            "rows_after_identity": after_identity,
            "rows_after_candidate_scope": after_candidate_scope,
            "rows_after_account_filter": after_account,
            "rows_after_risk_filter": after_risk,
            "rows_after_class_filter": after_class,
            "rows_after_fee_null_filter": after_class - fee_null_dropped if spec.sort_by == "total_fee" else after_class,
            "db_rows_after_filter_count": len(candidates),
            "ranked_rows_count": len(ranked),
            "candidate_count": len(ranked),
            "candidate_product_ids": [str(item.get("record_id") or "") for item in ranked],
            "matched_product_ids": [str(item.get("product_name") or "") for item in ranked],
            "resolved_variants": identity.get("resolved_variants") or [],
            "missing_variants": identity.get("missing_variants") or [],
            "empty_reason": identity.get("empty_reason") or (
                "irp_filter_zero" if spec.irp_only and after_account == 0 else
                "risk_filter_zero" if (
                    (spec.risk_grade_max is not None or spec.risk_tolerance or spec.allowed_risk_buckets)
                    and after_risk == 0
                ) else
                "no_matching_rows" if not ranked else None
            ),
            "ranking_policy": list(spec.ranking_policy or RANKING_POLICY_DEFAULT),
        }
        return ranked

    def parse_query(self, question: str, limit: int = 5) -> ProductQuerySpec:
        """검색과 Evidence 선택이 같은 결정적 조건을 공유하도록 노출합니다."""
        return self._parse_query(question, limit)

    def _load_records(self) -> list[dict[str, Any]]:
        if self.path.is_file():
            files = [self.path]
        elif self.path.is_dir():
            files = sorted(self.path.glob("*.json"))
        else:
            return []

        self.file_count = len(files)
        records: list[dict[str, Any]] = []

        for file_path in files:
            try:
                with file_path.open(encoding="utf-8") as file:
                    document = json.load(file)
            except (OSError, json.JSONDecodeError) as exc:
                self.load_errors.append(f"{file_path.name}: {exc}")
                self.validation_report["json_syntax_errors"] += 1
                continue
            self.validation_report["files_checked"] += 1
            try:
                validated = ProductJsonDocument.model_validate(document)
            except Exception as exc:
                self.load_errors.append(f"{file_path.name}: schema_validation_error: {type(exc).__name__}")
                self.validation_report["schema_errors"] += 1
                continue
            records.extend(
                self._normalize_document(
                    validated.model_dump(mode="python"),
                    file_path.name,
                )
            )

        return records

    def _records_from_documents(
        self,
        documents: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Normalize already-loaded Standard JSON payloads (Postgres or files)."""
        self.file_count = len(documents)
        records: list[dict[str, Any]] = []
        for json_filename, document in documents:
            self.validation_report["files_checked"] += 1
            prepared = prepare_prospectus_document(document, json_filename)
            try:
                validated = ProductJsonDocument.model_validate(prepared)
            except Exception as exc:
                self.load_errors.append(
                    f"{json_filename}: schema_validation_error: {type(exc).__name__}"
                )
                self.validation_report["schema_errors"] += 1
                continue
            records.extend(
                self._normalize_document(
                    validated.model_dump(mode="python"),
                    json_filename,
                )
            )
        return records

    def _normalize_document(
        self,
        document: dict[str, Any],
        json_filename: str,
    ) -> list[dict[str, Any]]:
        source_document = document.get("source_document") or {}
        product = document.get("product") or {}
        classes = document.get("classes") or []
        fees = document.get("fees") or []
        performance = document.get("performance") or []
        evidence_by_id = {
            item.get("evidence_id"): item
            for item in document.get("evidence") or []
            if item.get("evidence_id")
        }
        risk_rating = self._select_latest_risk_rating(
            document.get("risk_ratings") or []
        )
        investment_risks = [
            {
                "subject": item.get("subject"),
                "text": item.get("text"),
                "evidence_ids": list(item.get("evidence_ids") or []),
            }
            for item in document.get("narratives") or []
            if isinstance(item, dict)
            and str(item.get("narrative_type") or "").upper() == "INVESTMENT_RISK"
            and (item.get("subject") or item.get("text"))
        ]

        base_evidence_ids = [
            item["evidence_id"]
            for item in evidence_by_id.values()
            if item.get("field_path") in {"source_document", "product"}
        ]

        records: list[dict[str, Any]] = []
        for product_class in classes:
            if not isinstance(product_class, dict):
                continue

            pension_normalization = self.normalizer.normalize(
                RawPensionTypeContext(
                    pension_type_raw=product_class.get("pension_type"),
                    source_json_file=json_filename,
                    source_filename=source_document.get("filename"),
                    class_key=product_class.get("class_key"),
                    class_name=product_class.get("class_name"),
                    eligibility_text=product_class.get("eligibility_text"),
                    schema_version=str(document.get("schema_version", "")),
                    evidence_ids=list(product_class.get("evidence_ids") or []),
                    product_name=product.get("official_name"),
                )
            )
            pension_normalization_data = pension_normalization.model_dump(mode="json")
            irp_eligibility = evaluate_irp_eligibility(
                {
                    "pension_type_raw": product_class.get("pension_type"),
                    "pension_type": product_class.get("pension_type"),
                    "pension_type_codes": pension_normalization_data["pension_type_codes"],
                    "class_name": product_class.get("class_name"),
                    "eligibility_text": product_class.get("eligibility_text"),
                }
            )
            self.validation_report["normalizations"].append(
                {
                    "source_json_file": json_filename,
                    "class_key": product_class.get("class_key"),
                    "pension_type_raw": product_class.get("pension_type"),
                    **pension_normalization_data,
                }
            )

            class_key = product_class.get("class_key")
            class_fees = [
                fee
                for fee in fees
                if isinstance(fee, dict) and fee.get("class_key") == class_key
            ]
            class_performance = [
                item
                for item in performance
                if isinstance(item, dict) and item.get("class_key") == class_key
            ]
            total_fee_detail = self._select_total_fee(class_fees)

            evidence_ids = self._unique_values(
                [
                    *base_evidence_ids,
                    *(product_class.get("evidence_ids") or []),
                    *((risk_rating.get("evidence_ids") or []) if risk_rating else []),
                    *(evidence_id for fee in class_fees for evidence_id in fee.get("evidence_ids") or []),
                    *(
                        evidence_id
                        for item in class_performance
                        for evidence_id in item.get("evidence_ids") or []
                    ),
                    *(
                        (total_fee_detail.get("evidence_ids") or [])
                        if total_fee_detail
                        else []
                    ),
                ]
            )
            evidence = [
                deepcopy(evidence_by_id[evidence_id])
                for evidence_id in evidence_ids
                if evidence_id in evidence_by_id
            ]
            source_pages = self._unique_values(
                [item.get("page") for item in evidence if item.get("page") is not None]
            )

            class_code = product_class.get("kofia_fund_code")
            product_code = product.get("kofia_fund_code")
            identity = (
                f"class:{class_code}"
                if class_code
                else (
                    f"product:{product_code}|class:{class_key}|"
                    f"name:{product_class.get('class_name')}"
                )
            )

            records.append(
                {
                    "record_id": identity,
                    "product_kofia_fund_code": product_code,
                    "class_kofia_fund_code": class_code,
                    "product_name": product.get("official_name"),
                    "class_key": class_key,
                    "class_name": product_class.get("class_name"),
                    "manager_name": product.get("manager_name"),
                    "asset_type": product.get("asset_type"),
                    "legal_form": product.get("legal_form"),
                    "risk_grade": risk_rating.get("grade") if risk_rating else None,
                    "risk_label": risk_rating.get("label") if risk_rating else None,
                    "investment_risks": deepcopy(investment_risks),
                    "risk_as_of_date": (
                        risk_rating.get("as_of_date") if risk_rating else None
                    ),
                    # Keep the source value untouched for compatibility/audit;
                    # only derived fields drive eligibility-sensitive filtering.
                    "pension_type": product_class.get("pension_type"),
                    "pension_type_raw": product_class.get("pension_type"),
                    **pension_normalization_data,
                    "pension_type_normalization": pension_normalization_data,
                    "irp_eligibility_status": irp_eligibility["status"],
                    "irp_eligibility_reason": irp_eligibility["reason"],
                    "irp_eligibility_evidence_fields": irp_eligibility["evidence_fields"],
                    "irp_eligibility_rule_version": irp_eligibility["rule_version"],
                    "eligibility_text": product_class.get("eligibility_text"),
                    "channel": product_class.get("channel"),
                    "is_online": (
                        product_class.get("is_online")
                        if isinstance(product_class.get("is_online"), bool)
                        else None
                    ),
                    "sales_charge_type": product_class.get("sales_charge_type"),
                    "total_fee": (
                        total_fee_detail.get("rate") if total_fee_detail else None
                    ),
                    "total_fee_unit": (
                        total_fee_detail.get("unit") if total_fee_detail else None
                    ),
                    "total_fee_type": (
                        total_fee_detail.get("fee_type") if total_fee_detail else None
                    ),
                    "total_fee_as_of_date": (
                        total_fee_detail.get("as_of_date")
                        if total_fee_detail
                        else None
                    ),
                    "total_fee_detail": deepcopy(total_fee_detail)
                    if total_fee_detail
                    else None,
                    "fee_records": deepcopy(class_fees),
                    "performance": deepcopy(class_performance),
                    "as_of_date": source_document.get("as_of_date"),
                    "effective_date": source_document.get("effective_date"),
                    "source_file": source_document.get("filename"),
                    "source_json_file": json_filename,
                    "source_document": deepcopy(source_document),
                    "source_pages": source_pages,
                    "evidence_ids": evidence_ids,
                    "evidence": evidence,
                }
            )

        return records

    @classmethod
    def _select_total_fee(
        cls,
        class_fees: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for fee_type in cls.TOTAL_FEE_TYPES:
            candidates = [
                fee
                for fee in class_fees
                if str(fee.get("fee_type") or "").casefold() == str(fee_type).casefold()
                and isinstance(fee.get("rate"), (int, float))
            ]
            if candidates:
                return deepcopy(
                    max(
                        candidates,
                        key=lambda fee: (
                            fee.get("as_of_date") or "",
                            fee.get("effective_from") or "",
                        ),
                    )
                )
        return None

    @staticmethod
    def _select_latest_risk_rating(
        risk_ratings: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        candidates = [
            rating
            for rating in risk_ratings
            if isinstance(rating, dict)
            and isinstance(rating.get("grade"), int)
        ]
        if not candidates:
            return None

        return max(candidates, key=lambda rating: rating.get("as_of_date") or "")

    @staticmethod
    def _unique_values(values: list[Any]) -> list[Any]:
        result = []
        seen = set()
        for value in values:
            if value is not None and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _deduplicate_records(
        self,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(record["record_id"], []).append(record)

        deduplicated = []
        for variants in grouped.values():
            latest_as_of_date = max(
                record.get("as_of_date") or "" for record in variants
            )
            latest_variants = [
                record
                for record in variants
                if (record.get("as_of_date") or "") == latest_as_of_date
            ]
            selected = min(
                latest_variants,
                key=lambda record: record.get("source_file") or "",
            )
            result = deepcopy(selected)
            result["source_versions"] = [
                {
                    "source_file": variant.get("source_file"),
                    "source_json_file": variant.get("source_json_file"),
                    "as_of_date": variant.get("as_of_date"),
                    "effective_date": variant.get("effective_date"),
                    "source_pages": variant.get("source_pages"),
                    "evidence_ids": variant.get("evidence_ids"),
                }
                for variant in sorted(
                    variants,
                    key=lambda record: (
                        record.get("as_of_date") or "",
                        record.get("source_file") or "",
                    ),
                    reverse=True,
                )
            ]
            result["duplicate_source_count"] = len(variants)
            deduplicated.append(result)

        return deduplicated

    @staticmethod
    def _parse_query(question: str, limit: int) -> ProductQuerySpec:
        # Candidate-scope markers embed class names like "...-온라인-...".
        # Extract IDs first, then parse all constraints from the user text only.
        scope_match = re.search(r"\[후보ID:([^\]]+)\]", question)
        scoped_ids: tuple[str, ...] = ()
        if scope_match:
            raw_ids = scope_match.group(1)
            splitter = ";;" if ";;" in raw_ids else "|"
            scoped_ids = tuple(item for item in raw_ids.split(splitter) if item)
        question = re.sub(r"\s*\[후보ID:[^\]]+\]\s*", " ", question).strip()
        normalized = re.sub(r"\s+", "", question.upper())
        issues: list[str] = []
        requested_limit = re.search(r"(\d+)\s*(?:개|종)", question)
        parsed_limit = int(requested_limit.group(1)) if requested_limit else limit
        safe_limit = min(JsonProductDBAdapter.MAX_PRODUCT_LIMIT, max(1, parsed_limit))
        if parsed_limit != safe_limit:
            issues.append("limit_clamped_to_supported_range")

        risk_grade_max = None
        risk_grade_min = None
        risk_matches: list[tuple[int, str | None]] = []
        for pattern in (
            r"위험\s*등급(?:이)?\s*(\d+)\s*(?:등급)?\s*(이하|이내|까지|미만|이상|초과)?",
            r"(?<!\d)(\d+)\s*등급\s*(이하|이내|까지|미만|이상|초과)?",
        ):
            risk_matches.extend(
                (int(match.group(1)), match.group(2))
                for match in re.finditer(pattern, question)
            )
        # Require contiguous "위험등급" as a filter phrase. Product names often
        # contain "투자등급", and risk lookups like "...위험은?" must not be
        # treated as an unparseable numeric grade filter.
        if re.search(r"위험\s*등급", question) and not risk_matches:
            is_risk_lookup = bool(
                re.search(
                    r"(위험은|리스크는|투자위험|위험\s*등급(?:은|이|가)?\s*(?:\?|몇|뭐|무엇|어때|어떻)?)",
                    question,
                )
            ) and not any(
                token in question
                for token in ("이하", "이상", "미만", "초과", "이내", "까지", "보여줘", "추천")
            )
            if not is_risk_lookup:
                issues.append("risk_grade_not_parseable")
        parsed_risks: list[tuple[int | None, int | None]] = []
        for grade, comparator in risk_matches:
            if not JsonProductDBAdapter.MIN_RISK_GRADE <= grade <= JsonProductDBAdapter.MAX_RISK_GRADE:
                issues.append("risk_grade_out_of_supported_range")
                parsed_risks = []
                break
            if comparator == "미만":
                parsed_risks.append((grade - 1, None))
            elif comparator in {"이상", "초과"}:
                parsed_risks.append((None, grade if comparator == "이상" else grade + 1))
            else:
                # A bare "N등급" retains the existing meaning: at most N.
                parsed_risks.append((grade, None))
        if parsed_risks:
            if len(set(parsed_risks)) != 1:
                issues.append("conflicting_risk_grade_constraints")
            else:
                risk_grade_max, risk_grade_min = parsed_risks[0]
                if risk_grade_max is not None and risk_grade_max < JsonProductDBAdapter.MIN_RISK_GRADE:
                    risk_grade_max = None
                    issues.append("risk_grade_out_of_supported_range")
        risk_tolerance = None
        allowed_risk_buckets: tuple[str, ...] = ()
        excluded_risk_buckets: tuple[str, ...] = ()
        ranking_policy: tuple[str, ...] = ()
        if risk_grade_max is None:
            profile = re.search(
                r"위험성향\s*=\s*(conservative|moderate|aggressive)",
                question,
                re.IGNORECASE,
            )
            if profile:
                risk_tolerance = profile.group(1).lower()
                allowed_risk_buckets = allowed_buckets(risk_tolerance)
                excluded_risk_buckets = excluded_buckets(risk_tolerance)
                ranking_policy = RANKING_POLICY_DEFAULT

        fee_mentioned = any(term in normalized for term in ("총보수", "총비용", "보수", "수수료"))
        # Evaluate order words only in a fee request.  The negative lookbehind
        # keeps "싼 순" from matching the trailing characters of "비싼 순",
        # while allowing a later adjective to omit the repeated fee noun.
        asc_fee = fee_mentioned and bool(
            re.search(r"(?:낮은|적은|저렴한)\s*순|(?<!비)싼\s*순|오름차순|가장\s*(?:낮|적|저렴)|제일\s*(?:낮|적|저렴)", question)
        )
        desc_fee = fee_mentioned and bool(
            re.search(r"(?:높은|많은|비싼)\s*순|내림차순|가장\s*(?:높|많|비싸)|제일\s*(?:높|많|비싸)", question)
        )
        if asc_fee and desc_fee:
            issues.append("conflicting_total_fee_sort_order")
            sort_by, sort_order = "product_name", "asc"
        elif asc_fee:
            sort_by, sort_order = "total_fee", "asc"
        elif desc_fee:
            sort_by, sort_order = "total_fee", "desc"
        else:
            sort_by, sort_order = "product_name", "asc"
            if fee_mentioned and ("순" in question or "차순" in question):
                issues.append("total_fee_sort_order_not_parseable")

        performance_period = None
        performance_metric_type = None
        perf_mentioned = "수익률" in question
        one_year = bool(re.search(r"(?:최근\s*)?1\s*년|1Y", question, re.IGNORECASE))
        high_perf = perf_mentioned and bool(re.search(r"(?:높은|높게|상위|좋은).*?(?:순|상품|펀드)?|내림차순", question))
        low_perf = perf_mentioned and bool(re.search(r"(?:낮은|낮게).*?(?:순|상품|펀드)?|오름차순", question))
        if perf_mentioned and one_year and (high_perf or low_perf):
            sort_by = "performance"
            sort_order = "desc" if high_perf and not low_perf else "asc"
            performance_period = "1Y"
            performance_metric_type = "fund_return"

        candidate_record_ids = scoped_ids

        online_conflict = "온라인또는오프라인" in normalized or "오프라인또는온라인" in normalized
        if online_conflict:
            issues.append("conflicting_online_channel_constraint")

        tokens, aliases, tenors, name_required = JsonProductDBAdapter._parse_name_constraints(question)
        return ProductQuerySpec(
            irp_only=any(
                keyword in normalized
                for keyword in ("IRP", "개인형퇴직", "개인퇴직계좌")
            ),
            risk_grade_max=risk_grade_max,
            risk_grade_min=risk_grade_min,
            online_only=(not online_conflict) and any(
                keyword in normalized for keyword in ("온라인", "ONLINE", "비대면")
            ),
            sort_by=sort_by,
            sort_order=sort_order,
            performance_period=performance_period,
            performance_metric_type=performance_metric_type,
            candidate_record_ids=candidate_record_ids,
            limit=safe_limit,
            parse_issues=tuple(dict.fromkeys(issues)),
            name_tokens=tokens,
            family_aliases=aliases,
            tenors=tenors,
            name_match_required=name_required,
            risk_tolerance=risk_tolerance,
            allowed_risk_buckets=allowed_risk_buckets,
            excluded_risk_buckets=excluded_risk_buckets,
            ranking_policy=ranking_policy,
        )

    @staticmethod
    def _performance_value(record: dict[str, Any], metric_type: str, period: str) -> float | None:
        candidates: list[tuple[str, float]] = []
        for item in record.get("performance") or []:
            item_metric = item.get("metric_type") or item.get("metric")
            if str(item_metric or "").casefold() != metric_type.casefold():
                continue
            if str(item.get("period") or "").upper() != period.upper():
                continue
            value = item.get("value")
            if isinstance(value, (int, float)):
                candidates.append((str(item.get("as_of_date") or ""), float(value)))
        return max(candidates, default=("", None), key=lambda x: x[0])[1]

    @staticmethod
    def _normalize_catalog_text(value: Any) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).casefold()

    @staticmethod
    def _parse_name_constraints(question: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]:
        parts = re.findall(r"[A-Za-z0-9가-힣]+", question or "")
        tenors: list[str] = []
        for part in parts:
            if part in {"초단기", "중장기", "단기", "장기"} and part not in tenors:
                tenors.append(part)
        aliases: list[str] = []
        if "tdf" in JsonProductDBAdapter._normalize_catalog_text(question):
            aliases.extend(["tdf", "라이프사이클"])
        generic = {
            "상품", "펀드", "증권", "투자신탁", "자투자신탁", "모투자신탁", "채권", "주식",
            "연금", "미래에셋", "보여줘", "달라요", "원해요", "안정적인", "뭐가",
            "순서대로", "낮은", "높은", "총보수가", "중에서", "그중에서",
        }
        generic_norm = {JsonProductDBAdapter._normalize_catalog_text(item) for item in generic}
        tokens: list[str] = []
        for part in parts:
            if part in {"초단기", "중장기", "단기", "장기"}:
                continue
            if JsonProductDBAdapter._normalize_catalog_text(part) in generic_norm:
                continue
            if len(part) < 2:
                continue
            if part.upper() == "TDF":
                continue
            tokens.append(part)
        required = bool(aliases or (tokens and tenors) or any(token in (question or "") for token in ("솔로몬",)))
        return tuple(tokens), tuple(aliases), tuple(tenors), required

    @staticmethod
    def _tenor_in_name(product_name: str, tenor: str) -> bool:
        name = str(product_name or "")
        if tenor == "초단기":
            return "초단기" in name
        if tenor == "중장기":
            return "중장기" in name
        if tenor == "단기":
            return "단기" in name and "초단기" not in name and "중장기" not in name
        if tenor == "장기":
            return "장기" in name and "중장기" not in name and "초단기" not in name
        return tenor in name

    @staticmethod
    def _matches_name_constraint(record: dict[str, Any], spec: ProductQuerySpec) -> bool:
        name = str(record.get("product_name") or "")
        folded = JsonProductDBAdapter._normalize_catalog_text(name)
        if spec.exact_product_names:
            return any(
                JsonProductDBAdapter._normalize_catalog_text(item) == folded
                or JsonProductDBAdapter._normalize_catalog_text(item) in folded
                for item in spec.exact_product_names
            )
        if spec.family_aliases:
            if not any(JsonProductDBAdapter._normalize_catalog_text(alias) in folded for alias in spec.family_aliases):
                return False
        if spec.name_tokens:
            if not all(JsonProductDBAdapter._normalize_catalog_text(token) in folded for token in spec.name_tokens):
                return False
        if spec.tenors:
            if not any(JsonProductDBAdapter._tenor_in_name(name, tenor) for tenor in spec.tenors):
                return False
        if spec.name_match_required and not (spec.family_aliases or spec.name_tokens or spec.exact_product_names):
            return False
        return True

    def _resolve_catalog_identity(
        self,
        question: str,
        candidates: list[dict[str, Any]],
        spec: ProductQuerySpec,
    ) -> dict[str, Any]:
        """Resolve named/family products before ranking. Never substitute."""
        q = self._normalize_catalog_text(question)
        if not q:
            return {"rows": candidates, "one_row_per_product": False, "filter_label": None}

        exact = [
            record for record in candidates
            if self._normalize_catalog_text(record.get("product_name"))
            and self._normalize_catalog_text(record.get("product_name")) in q
        ]
        if exact:
            names = sorted({str(item.get("product_name") or "") for item in exact})
            return {
                "rows": exact,
                "one_row_per_product": True,
                "filter_label": "exact_canonical",
                "resolved_variants": names,
            }

        if spec.name_match_required:
            matched = [record for record in candidates if self._matches_name_constraint(record, spec)]
            if not matched:
                return {
                    "rows": [],
                    "one_row_per_product": True,
                    "filter_label": "family_or_alias",
                    "empty_reason": "named_product_not_in_catalog",
                    "missing_variants": list(spec.tenors) or list(spec.name_tokens) or list(spec.family_aliases),
                }
            resolved: list[dict[str, Any]] = []
            missing: list[str] = []
            found: list[str] = []
            if spec.tenors:
                for tenor in spec.tenors:
                    variant_spec = ProductQuerySpec(
                        name_tokens=spec.name_tokens,
                        family_aliases=spec.family_aliases,
                        tenors=(tenor,),
                        name_match_required=True,
                    )
                    hits = [record for record in matched if self._matches_name_constraint(record, variant_spec)]
                    if hits:
                        picked = self._one_row_per_product(hits, spec.sort_by)
                        resolved.extend(picked)
                        found.extend(str(item.get("product_name") or "") for item in picked)
                    else:
                        missing.append(tenor)
                return {
                    "rows": resolved,
                    "one_row_per_product": False,
                    "filter_label": "family_variants",
                    "resolved_variants": found,
                    "missing_variants": missing,
                    "empty_reason": "named_product_not_in_catalog" if not resolved else None,
                }
            return {
                "rows": matched,
                "one_row_per_product": True,
                "filter_label": "alias_or_family",
                "resolved_variants": sorted({str(item.get("product_name") or "") for item in matched}),
            }

        scoped = self._apply_catalog_identity_scope(question, candidates)
        return {"rows": scoped, "one_row_per_product": False, "filter_label": None}

    def _one_row_per_product(self, rows: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in rows:
            grouped.setdefault(str(record.get("product_name") or record.get("record_id")), []).append(record)
        picked: list[dict[str, Any]] = []
        for variants in grouped.values():
            def _class_rank(record: dict[str, Any]) -> tuple[int, float, str]:
                class_name = str(record.get("class_name") or "")
                pension_class = 0 if re.search(r"C-?P2|퇴직연금|개인연금|C-?P\b", class_name) else 1
                fee = record.get("total_fee")
                fee_key = float(fee) if isinstance(fee, (int, float)) else 9999.0
                return (pension_class, fee_key if sort_by == "total_fee" else 0.0, class_name)
            picked.append(min(variants, key=_class_rank))
        return picked

    def _apply_catalog_identity_scope(
        self, question: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Fallback lexical family scope used only when name_match is not required."""
        q = self._normalize_catalog_text(question)
        if not q:
            return candidates
        if "tdf" in q:
            scoped = [
                record for record in candidates
                if any(key in self._normalize_catalog_text(record.get("product_name")) for key in ("tdf", "라이프사이클"))
            ]
            if scoped:
                return scoped
        # Only distinctive catalog family names may narrow identity. Generic
        # recommendation/slot text such as '10년' or '추천해줘' must not.
        if not any(hint in (question or "") for hint in ("솔로몬",)):
            return candidates
        generic = {"상품", "펀드", "증권", "투자신탁", "자투자신탁", "채권", "주식", "국공채", "단기", "중장기", "장기", "연금", "미래에셋"}
        tokens = [
            token for token in re.findall(r"[A-Za-z0-9가-힣]{2,}", question)
            if self._normalize_catalog_text(token) not in {self._normalize_catalog_text(item) for item in generic}
        ]
        matched_tokens = []
        for token in tokens:
            normalized = self._normalize_catalog_text(token)
            if normalized and any(normalized in self._normalize_catalog_text(record.get("product_name")) for record in candidates):
                matched_tokens.append(normalized)
        if matched_tokens:
            scoped = [
                record for record in candidates
                if all(token in self._normalize_catalog_text(record.get("product_name")) for token in matched_tokens)
            ]
            if scoped:
                if all(key in question for key in ("단기", "중장기", "장기")) and "초단기" not in question:
                    scoped = [record for record in scoped if "초단기" not in str(record.get("product_name") or "")]
                return scoped or candidates
        return candidates

    @staticmethod
    def _is_irp_eligible(record: dict[str, Any]) -> bool:
        return evaluate_irp_eligibility(record)["status"] == "ELIGIBLE"


def prepare_prospectus_document(
    document: dict[str, Any],
    json_filename: str,
) -> dict[str, Any]:
    """Fill document_id/filename so mirae Standard JSON joins PDF chunks."""
    prepared = deepcopy(document)
    source = dict(prepared.get("source_document") or {})
    stem = Path(json_filename).name.split(".")[0]
    document_id = source.get("document_id") or stem
    filename = source.get("filename") or f"{document_id}.pdf"
    if not str(filename).lower().endswith(".pdf"):
        filename = f"{document_id}.pdf"
    source["document_id"] = document_id
    source["filename"] = filename
    prepared["source_document"] = source
    if not prepared.get("schema_version"):
        prepared["schema_version"] = "0.1"
    prepared.setdefault("product", {})
    prepared.setdefault("classes", [])
    return prepared


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    from .paths import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env", override=False)


def _default_database_url() -> str:
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("PENSION_DATABASE_URL")
        or "postgresql://postgres:postgres@127.0.0.1:5432/pension_agent"
    )


def _mirae_standard_json_dir() -> Path:
    override = os.getenv("STANDARD_JSON_DIR")
    if override:
        return Path(override)
    from .paths import REPO_ROOT

    return REPO_ROOT / "data" / "standard_json"


def _load_postgres_documents(database_url: str) -> tuple[list[tuple[str, dict[str, Any]]], str] | None:
    try:
        import psycopg
    except ImportError:
        return None
    try:
        with psycopg.connect(database_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT document_id, filename, standard_json, updated_at
                    FROM source_documents
                    ORDER BY document_id
                    """
                )
                rows = cur.fetchall()
    except Exception:
        return None
    if not rows:
        return None
    documents: list[tuple[str, dict[str, Any]]] = []
    stamps: list[str] = []
    for document_id, filename, payload, updated_at in rows:
        if not isinstance(payload, dict):
            continue
        json_name = f"{document_id}.schema_v0.1.json"
        if filename and not payload.get("source_document", {}).get("filename"):
            payload = {
                **payload,
                "source_document": {
                    **(payload.get("source_document") or {}),
                    "filename": filename,
                    "document_id": document_id,
                },
            }
        documents.append((json_name, payload))
        stamps.append(f"{document_id}:{updated_at}")
    if not documents:
        return None
    version = "postgres:" + sha256(json.dumps(documents, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return documents, version


def _load_standard_json_documents(directory: Path) -> list[tuple[str, dict[str, Any]]]:
    if not directory.is_dir():
        return []
    documents: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.schema_v0.1.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            documents.append((path.name, payload))
    return documents


def create_product_db_adapter(
    normalizer: ProductNormalizer | None = None,
    fallback_path: str | Path | None = None,
) -> JsonProductDBAdapter:
    """Prefer mirae Postgres prospectus data, then Standard JSON, then local GPT JSON."""
    _load_dotenv_files()
    from .paths import REPO_ROOT

    fallback = Path(fallback_path) if fallback_path else REPO_ROOT / "data" / "structured" / "products"
    requested = (os.getenv("PRODUCT_DB_BACKEND") or "auto").strip().lower()
    database_url = _default_database_url()

    if requested in {"auto", "postgres"}:
        loaded = _load_postgres_documents(database_url)
        if loaded:
            documents, version = loaded
            return JsonProductDBAdapter(
                fallback,
                normalizer=normalizer,
                documents=documents,
                backend="postgres",
                source_version=version,
            )
        if requested == "postgres":
            raise RuntimeError("configured_product_backend_unavailable:postgres")

    if requested in {"auto", "standard_json"}:
        standard_dir = _mirae_standard_json_dir()
        documents = _load_standard_json_documents(standard_dir)
        if documents:
            return JsonProductDBAdapter(
                standard_dir,
                normalizer=normalizer,
                documents=documents,
                backend="standard_json",
                source_version="standard_json:" + sha256(json.dumps(documents, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest(),
            )
        if requested == "standard_json":
            raise RuntimeError("configured_product_backend_unavailable:standard_json")

    return JsonProductDBAdapter(fallback, normalizer=normalizer, backend="json")
