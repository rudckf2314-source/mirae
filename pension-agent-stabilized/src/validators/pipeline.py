from processing.post_processor import recompute_final_warnings
from schemas.chunk import Chunk
from schemas.document import DetectedTable
from schemas.product import CanonicalProduct, ValidationReport

from .completeness_validator import CompletenessValidator
from .canonical_validator import CanonicalValidator
from .source_validator import SourceValidator
from .status import compute_final_status, partition_messages
from .ownership_validator import OwnershipValidator


class ValidationPipeline:
    def __init__(self):
        self.canonical_validator = CanonicalValidator()
        self.source_validator = SourceValidator()
        self.completeness_validator = CompletenessValidator()
        self.ownership_validator = OwnershipValidator()

    def validate(
        self,
        product: CanonicalProduct,
        chunks: list[Chunk],
        tables: list[DetectedTable] | None = None,
    ) -> CanonicalProduct:
        product = self.ownership_validator.validate(product)
        product.extraction.warnings = recompute_final_warnings(product)
        warnings: list[str] = list(product.extraction.warnings)
        report = ValidationReport()

        source_warnings, product = self.source_validator.validate(product, chunks)
        warnings.extend(source_warnings)
        report.evidence_status = "WARNING" if source_warnings else "PASS"
        if any("invalid evidence_ref" in item for item in source_warnings) and not product.all_evidence_refs():
            report.evidence_status = "FAIL"

        completeness_warnings, missing = self.completeness_validator.validate(
            product, chunks, tables
        )
        warnings.extend(completeness_warnings)
        from .status import message_severity as _sev

        actionable_completeness = [
            item for item in completeness_warnings if _sev(item) in {"WARNING", "ERROR"}
        ]
        report.completeness_status = (
            "WARNING" if actionable_completeness or missing else "PASS"
        )

        canonical_result = self.canonical_validator.validate(product)
        warnings.extend(canonical_result.schema_warnings)
        warnings.extend(canonical_result.consistency_warnings)
        report.schema_status = (
            "FAIL"
            if any(item.startswith("schema_validation_failed") for item in canonical_result.schema_warnings)
            else "PASS"
        )
        report.consistency_status = (
            "WARNING" if canonical_result.consistency_warnings else "PASS"
        )

        warnings = list(dict.fromkeys(warnings))
        missing = list(dict.fromkeys(missing))
        if missing and report.completeness_status == "PASS":
            report.completeness_status = "WARNING"

        recomputed = recompute_final_warnings(product, warnings)
        active, info, audit = partition_messages(
            [*product.extraction.audit, *product.extraction.info, *recomputed]
        )
        product.extraction.warnings = active
        product.extraction.info = list(dict.fromkeys(info))
        product.extraction.audit = list(dict.fromkeys(audit))
        product.extraction.missing_fields = missing
        product.extraction.validation = report
        product.extraction.status = compute_final_status(product, report, missing, product.extraction.warnings)
        return product
