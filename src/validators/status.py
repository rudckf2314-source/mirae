from __future__ import annotations

from schemas.product import CanonicalProduct, ValidationReport

AUDIT_PREFIXES = (
    "DETERMINISM_FINGERPRINT:",
    "OBJECTIVE_RECOVERY_TRACE:",
    "AUDIT:",
)
INFO_PREFIXES = (
    "INFO:",
)
AUDIT_MARKERS = (
    "RECOVERY_TRACE",
    "RESELECTION_TRACE",
    "fallback used",
    "fingerprint",
    "NARRATIVE_RESELECTED:",
)
INFO_MARKERS = (
    "SOURCE_ABSENT",
    "NOT_APPLICABLE",
    "metadata_missing_date:",
    "정상 NOT_FOUND",
)
ERROR_MARKERS = (
    "schema_validation_failed",
    "evidence referential integrity failed",
    "FINAL_STATE_MISSING:",
)


def message_severity(message: str) -> str:
    text = message or ""
    if any(text.startswith(prefix) for prefix in INFO_PREFIXES) or any(
        marker in text for marker in INFO_MARKERS
    ):
        return "INFO"
    if any(text.startswith(prefix) for prefix in AUDIT_PREFIXES):
        return "AUDIT"
    if any(marker in text for marker in AUDIT_MARKERS):
        return "AUDIT"
    if "Original investment_objective text could not be verified" in text and "replaced with" in text:
        return "AUDIT"
    if any(marker in text for marker in ERROR_MARKERS):
        return "ERROR"
    if "실패:" in text or text.lower().startswith("error"):
        # Intermediate LLM/API failures are audit unless final state is missing.
        return "AUDIT"
    return "WARNING"


def partition_messages(
    messages: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Split messages into warnings / info / audit (order-preserving, deduped)."""
    warnings: list[str] = []
    info: list[str] = []
    audit: list[str] = []
    for item in dict.fromkeys(messages):
        severity = message_severity(item)
        if severity == "INFO":
            info.append(item)
        elif severity == "AUDIT":
            audit.append(item)
        else:
            # WARNING and ERROR stay in operational warnings for consumers.
            warnings.append(item)
    return warnings, info, audit


def actionable_warnings(warnings: list[str]) -> list[str]:
    return [item for item in warnings if message_severity(item) in {"WARNING", "ERROR"}]


def compute_final_status(
    product: CanonicalProduct,
    report: ValidationReport,
    missing: list[str],
    warnings: list[str],
) -> str:
    core_missing = any(
        field in missing
        for field in ("product.name", "product.manager", "product.risk.grade", "document.file_name")
    )
    if report.schema_status == "FAIL" or report.evidence_status == "FAIL" or core_missing:
        return "failed"
    if any(message_severity(item) == "ERROR" for item in warnings):
        return "failed"
    if missing:
        report.completeness_status = "WARNING"
    active = actionable_warnings(warnings)
    if (
        report.completeness_status != "PASS"
        or report.consistency_status != "PASS"
        or report.evidence_status != "PASS"
        or missing
        or active
    ):
        return "warning"
    return "success"
