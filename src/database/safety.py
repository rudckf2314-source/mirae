from __future__ import annotations

from schemas.product_schema import ProductExtraction


class UnsafePersistenceError(ValueError):
    pass


def persistence_blockers(product: ProductExtraction) -> list[str]:
    blockers: list[str] = []
    quality = product.quality_control
    if quality.verification_status == "FAIL" or quality.verification_fail_count:
        blockers.append(
            f"verification={quality.verification_status}, fail_count={quality.verification_fail_count}"
        )
    if quality.contradicted_fields:
        blockers.append(f"contradicted_fields={quality.contradicted_fields}")

    critical = {"classes", "fees", "sales_charges"}
    unsafe = {"AMBIGUOUS", "CONFLICT", "PARSE_FAILED"}
    for field in critical:
        status = product.field_status.get(field)
        if status is not None and status.value in unsafe:
            blockers.append(f"{field}={status.value}")

    for issue in product.extraction_issues:
        path = issue.field_path.split(".", 1)[0]
        message = issue.message.lower()
        if path in critical and (
            "source signal detected" in message
            or "표가 탐지" in issue.message
            or "owner_unresolved" in message
        ):
            blockers.append(f"{path}: source detected but unresolved")
    return list(dict.fromkeys(blockers))


def assert_persistence_safe(product: ProductExtraction) -> None:
    blockers = persistence_blockers(product)
    if blockers:
        raise UnsafePersistenceError("DB 적재 차단: " + "; ".join(blockers))
