"""Fail-closed, shared IRP eligibility policy for product search and verification."""
from __future__ import annotations

import re
from typing import Any, Literal, TypedDict


IRP_ELIGIBILITY_RULE_VERSION = "irp-eligibility-v1"
IRPStatus = Literal["ELIGIBLE", "INELIGIBLE", "UNRESOLVED"]


class IRPEligibility(TypedDict):
    status: IRPStatus
    reason: str
    evidence_fields: list[str]
    rule_version: str


_POSITIVE_CODES = {"IRP", "INDIVIDUAL_RETIREMENT_PENSION", "PERSONAL_RETIREMENT_PENSION"}
_POSITIVE_KOREAN = ("개인형퇴직연금", "개인퇴직계좌")
# KOFIA/FSS share-class codes stored on classes.class_name. C-P2 / C-P2e are
# the registered 퇴직연금 classes that IRP accounts actually subscribe to.
_POSITIVE_CLASS_CODES = re.compile(r"(?:^|[^A-Za-z])C-?P2e?(?:[^A-Za-z]|$)", re.IGNORECASE)
_NEGATIVE_ONLY = re.compile(
    r"(?:DB|DC)\s*형\s*(?:전용|가입자\s*(?:전용|만)|만(?:의|을)?)",
    re.IGNORECASE,
)


def evaluate_irp_eligibility(record: dict[str, Any]) -> IRPEligibility:
    """Return IRP eligibility using explicit source evidence only.

    Retirement-pension labels and ``RETIREMENT_PENSION`` are deliberately not
    positive evidence: they identify a broader pension category, not IRP.
    """
    field_values = {
        "pension_type_raw": record.get("pension_type_raw"),
        "pension_type": record.get("pension_type"),
        "class_name": record.get("class_name"),
        "eligibility_text": record.get("eligibility_text"),
    }
    positive_fields: list[str] = []
    negative_fields: list[str] = []

    codes = record.get("pension_type_codes") or []
    if isinstance(codes, str):
        codes = [codes]
    if any(str(code).strip().upper() in _POSITIVE_CODES for code in codes):
        positive_fields.append("pension_type_codes")

    for field, value in field_values.items():
        text = str(value or "")
        collapsed = re.sub(r"\s+", "", text)
        if re.search(r"\bIRP\b", text, flags=re.IGNORECASE) or any(
            phrase in collapsed for phrase in _POSITIVE_KOREAN
        ):
            positive_fields.append(field)
        if field == "class_name" and _POSITIVE_CLASS_CODES.search(text):
            positive_fields.append(field)
        if _NEGATIVE_ONLY.search(text):
            negative_fields.append(field)

    evidence_fields = [*dict.fromkeys([*positive_fields, *negative_fields])]
    if positive_fields and negative_fields:
        return _result("UNRESOLVED", "conflicting explicit IRP and DB/DC-only evidence", evidence_fields)
    if positive_fields:
        return _result("ELIGIBLE", "explicit IRP eligibility evidence", evidence_fields)
    if negative_fields:
        return _result("INELIGIBLE", "explicit DB/DC-only ineligibility evidence", evidence_fields)
    return _result("UNRESOLVED", "no explicit IRP eligibility evidence", evidence_fields)


def _result(status: IRPStatus, reason: str, evidence_fields: list[str]) -> IRPEligibility:
    return {
        "status": status,
        "reason": reason,
        "evidence_fields": evidence_fields,
        "rule_version": IRP_ELIGIBILITY_RULE_VERSION,
    }

