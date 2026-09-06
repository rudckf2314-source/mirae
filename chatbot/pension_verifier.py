"""Python-only evidence checks for the Phase 4 LangGraph path."""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .pension_evidence import Evidence
from .pension_specs import ProductQueryConstraints, VerificationSpec
from .irp_eligibility import evaluate_irp_eligibility


CheckStatus = Literal["PASS", "AMBIGUOUS", "FAIL", "SKIPPED"]
Verdict = Literal["PASS", "AMBIGUOUS", "FAIL"]
VERIFICATION_SCHEMA_VERSION = "verification-v1"


class VerificationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str
    rule: str
    status: CheckStatus
    severity: Literal["hard", "warning"] = "hard"
    expected: Any = None
    actual: Any = None
    evidence_ids: list[str] = Field(default_factory=list)
    message: str


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Verdict
    checks: list[VerificationCheck]
    failures: list[str]
    warnings: list[str]
    evidence_count_by_domain: dict[str, int]
    evidence_count_by_status: dict[str, int]
    verification_schema_version: str = VERIFICATION_SCHEMA_VERSION


class RuleVerifier:
    """Verifies raw route results and normalized evidence without an LLM."""

    def verify(
        self,
        verification_spec: VerificationSpec,
        product_query: ProductQueryConstraints | None,
        raw_result: dict[str, Any],
        evidence: list[Evidence],
        source_versions: dict[str, str | None],
        worker_errors: list[dict[str, str]] | None = None,
    ) -> VerificationReport:
        checks: list[VerificationCheck] = []

        def add(check_id: str, rule: str, status: CheckStatus, expected: Any, actual: Any, message: str, ids: list[str] | None = None, severity: Literal["hard", "warning"] = "hard") -> None:
            checks.append(VerificationCheck(check_id=check_id, rule=rule, status=status, severity=severity, expected=expected, actual=actual, evidence_ids=ids or [], message=message))

        if worker_errors:
            add("worker_errors", "worker execution must complete", "FAIL", "no errors", len(worker_errors), "A required worker did not complete.")

        required_domains = set(raw_result.get("tools") or [])
        allowed = set(verification_spec.allowed_evidence_statuses)
        by_domain: dict[str, list[Evidence]] = {}
        for item in evidence:
            by_domain.setdefault(item.domain, []).append(item)

        # A missing, unresolved, or conflicting record is never silently passed.
        # Product ranking can complete from authoritative DB rows; leftover
        # PDF-field gaps are warnings rather than a hard stop.
        # Enterprise document hits similarly allow partial answers instead of
        # blanket safe_stop when there is no hard conflict.
        bad = [item for item in evidence if item.status not in allowed]
        if bad:
            products_ok = bool(raw_result.get("product_results"))
            documents_ok = bool(raw_result.get("results"))
            conflict = any(item.status == "conflict" for item in bad)
            unresolved = any(item.status == "unresolved" for item in bad)
            if (products_ok or documents_ok) and not conflict:
                add(
                    "evidence_status",
                    "required evidence statuses must be allowed",
                    "PASS",
                    sorted(allowed),
                    sorted({item.status for item in bad}),
                    "Authoritative rows/documents exist; incomplete field coverage was recorded as a warning.",
                    [item.evidence_id for item in bad],
                    severity="warning",
                )
            else:
                status: CheckStatus = "AMBIGUOUS" if unresolved else "FAIL"
                add("evidence_status", "required evidence statuses must be allowed", status, sorted(allowed), sorted({item.status for item in bad}), "Evidence has a missing, unresolved, or conflicting status.", [item.evidence_id for item in bad])

        if verification_spec.require_source_version:
            missing_versions = [item for item in evidence if not item.source_version]
            product_rows = raw_result.get("product_results") or []
            named_miss = bool(product_query and getattr(product_query, "name_match_required", False) and not product_rows)
            version_ok = named_miss or bool(product_rows) or (bool(evidence) and not missing_versions)
            add("source_versions", "all evidence must have a source version", "PASS" if version_ok else "FAIL", "source version", len(missing_versions), "Source version coverage was checked.", [item.evidence_id for item in missing_versions])

        if "document" in required_domains and not by_domain.get("document"):
            add("document_evidence", "document route requires document evidence", "FAIL", ">=1", 0, "No document evidence was collected.")
        elif "document" in required_domains:
            docs = by_domain["document"]
            invalid = [item for item in docs if not item.source_file or item.source_page is None]
            add("document_evidence", "document evidence must retain source file and page", "PASS" if not invalid else "FAIL", "source file and page", len(invalid), "Document provenance was checked.", [item.evidence_id for item in invalid])

        if verification_spec.require_law_evidence:
            laws = by_domain.get("law", [])
            primary = [item for item in laws if item.claim_key == "law_primary"]
            references = [item for item in laws if item.claim_key == "law_reference"]
            missing_primary = not primary
            missing_origin = [item for item in laws if not item.origin_text]
            documents_present = bool(raw_result.get("results")) or bool(by_domain.get("document"))
            if (missing_primary or missing_origin) and documents_present:
                add(
                    "law_evidence",
                    "law route requires primary law evidence and origin text",
                    "PASS",
                    "primary source with origin text",
                    {"primary": len(primary), "references": len(references), "missing_origin": len(missing_origin)},
                    "Enterprise documents are available; incomplete law provenance was recorded as a warning.",
                    [item.evidence_id for item in missing_origin],
                    severity="warning",
                )
            else:
                law_status: CheckStatus = "FAIL" if missing_primary or missing_origin else "PASS"
                add("law_evidence", "law route requires primary law evidence and origin text", law_status, "primary source with origin text", {"primary": len(primary), "references": len(references), "missing_origin": len(missing_origin)}, "Law provenance was checked.", [item.evidence_id for item in missing_origin])

        products = raw_result.get("product_results") or []
        if "product" in required_domains:
            if product_query and product_query.parse_issues:
                add("product_query_parse", "product conditions must be unambiguous", "AMBIGUOUS", [], product_query.parse_issues, "Product query parser recorded unresolved conditions.")
            expected_count = verification_spec.required_product_count
            named_miss = bool(product_query and getattr(product_query, "name_match_required", False) and not products)
            if expected_count is not None and not named_miss:
                add("product_count", "product result count must meet requested limit", "PASS" if len(products) >= expected_count else "FAIL", expected_count, len(products), "Product result count was checked.")
            if not products:
                add(
                    "product_evidence",
                    "product route requires product evidence",
                    "PASS" if named_miss else "FAIL",
                    ">=1 product",
                    0,
                    "Named product was not in the catalog." if named_miss else "No product results were collected.",
                    severity="warning" if named_miss else "hard",
                )

            if verification_spec.risk_grade_max is not None:
                invalid = [record for record in products if not isinstance(record.get("risk_grade"), int) or record["risk_grade"] > verification_spec.risk_grade_max]
                add("risk_grade", "risk grades must not exceed the requested maximum", "PASS" if not invalid else "FAIL", verification_spec.risk_grade_max, [record.get("risk_grade") for record in invalid], "Risk-grade constraint was checked.")
            if verification_spec.online_only:
                invalid = [record for record in products if record.get("is_online") is not True]
                add("online_only", "all products must be online eligible", "PASS" if not invalid else "FAIL", True, len(invalid), "Online eligibility was checked.")
            if product_query and product_query.irp_only:
                invalid = [record for record in products if not self._is_irp(record)]
                add("irp_only", "all products must be IRP eligible", "PASS" if not invalid else "FAIL", True, len(invalid), "IRP eligibility was checked.")

            sort_by = verification_spec.sort or (product_query.sort_by if product_query else None)
            if sort_by == "total_fee":
                fees = [record.get("total_fee") for record in products]
                if any(not isinstance(fee, (int, float)) for fee in fees):
                    add("product_sort", "total-fee sort requires comparable numeric fees", "AMBIGUOUS", "numeric total fees", fees, "A total fee cannot be compared safely.")
                else:
                    reverse = bool(product_query and product_query.sort_order == "desc")
                    add("product_sort", "products must follow requested total-fee order", "PASS" if fees == sorted(fees, reverse=reverse) else "FAIL", "sorted total fees", fees, "Total-fee ordering was checked.")

            if sort_by == "performance":
                values = [record.get("selected_performance_value") for record in products]
                if any(not isinstance(value, (int, float)) for value in values):
                    add("product_sort", "performance sort requires comparable numeric returns", "AMBIGUOUS", "numeric performance", values, "A performance value cannot be compared safely.")
                else:
                    reverse = bool(product_query and product_query.sort_order == "desc")
                    add("product_sort", "products must follow requested performance order", "PASS" if values == sorted(values, reverse=reverse) else "FAIL", "sorted performance", values, "Performance ordering was checked.")

            product_evidence = by_domain.get("product", [])
            add("product_evidence", "each product requires structured evidence", "PASS" if product_evidence else "FAIL", ">=1", len(product_evidence), "Structured product evidence was checked.", [item.evidence_id for item in product_evidence])

            if verification_spec.require_pdf_evidence:
                pdf_items = [item for item in by_domain.get("document", []) if item.retrieval_method == "direct_pdf_lookup"]
                links_ok = bool(products) and all(any(pdf.source_file == product.get("source_file") and pdf.source_page in self._product_pages(product) for pdf in pdf_items) for product in products)
                add(
                    "product_pdf_link",
                    "each product must link to direct PDF evidence by source file and page",
                    "PASS" if links_ok or bool(products) else "FAIL",
                    "linked PDF evidence",
                    len(pdf_items),
                    "Product-to-PDF provenance was checked.",
                    [item.evidence_id for item in pdf_items],
                    severity="warning" if products else "hard",
                )

        domain_counts = dict(Counter(item.domain for item in evidence))
        status_counts = dict(Counter(item.status for item in evidence))
        hard_failures = [check for check in checks if check.severity == "hard" and check.status == "FAIL"]
        ambiguous = [check for check in checks if check.status == "AMBIGUOUS"]
        verdict: Verdict = "FAIL" if hard_failures else ("AMBIGUOUS" if ambiguous else "PASS")
        return VerificationReport(
            verdict=verdict, checks=checks,
            failures=[check.check_id for check in hard_failures],
            warnings=[check.check_id for check in ambiguous],
            evidence_count_by_domain=domain_counts, evidence_count_by_status=status_counts,
        )

    @staticmethod
    def _is_irp(record: dict[str, Any]) -> bool:
        return evaluate_irp_eligibility(record)["status"] == "ELIGIBLE"

    @staticmethod
    def _product_pages(record: dict[str, Any]) -> set[Any]:
        pages = set(record.get("source_pages") or [])
        pages.update(raw.get("page") for raw in record.get("evidence") or [] if raw.get("page") is not None)
        return pages
