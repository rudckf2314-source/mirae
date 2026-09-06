"""Semantic risk mapping and recommendation ranking.

Grade direction is taken from prospectus labels, not numeric inequality.
This is candidate filtering, not a suitability recommendation.
"""
from __future__ import annotations

from typing import Any


# Repeated across Standard JSON / 투자설명서 summary pages.
PROSPECTUS_GRADE_LABELS = {
    1: "매우 높은 위험",
    2: "높은 위험",
    3: "다소 높은 위험",
    4: "보통 위험",
    5: "낮은 위험",
    6: "매우 낮은 위험",
}

GRADE_TO_BUCKET = {
    1: "VERY_AGGRESSIVE",
    2: "AGGRESSIVE",
    3: "MODERATE_AGGRESSIVE",
    4: "MODERATE",
    5: "CONSERVATIVE",
    6: "VERY_CONSERVATIVE",
}

LABEL_TO_BUCKET = {
    "매우 높은 위험": "VERY_AGGRESSIVE",
    "매우높은위험": "VERY_AGGRESSIVE",
    "높은 위험": "AGGRESSIVE",
    "높은위험": "AGGRESSIVE",
    "다소 높은 위험": "MODERATE_AGGRESSIVE",
    "다소높은위험": "MODERATE_AGGRESSIVE",
    "보통 위험": "MODERATE",
    "보통위험": "MODERATE",
    "낮은 위험": "CONSERVATIVE",
    "낮은위험": "CONSERVATIVE",
    "매우 낮은 위험": "VERY_CONSERVATIVE",
    "매우낮은위험": "VERY_CONSERVATIVE",
}

# Qualitative user slots → semantic buckets.
# No enterprise suitability rule was found; this is filter policy only.
RISK_TOLERANCE_POLICY = {
    "conservative": {
        "preferred": ("CONSERVATIVE", "VERY_CONSERVATIVE"),
        "acceptable": ("MODERATE",),
        "exclude": ("VERY_AGGRESSIVE", "AGGRESSIVE", "MODERATE_AGGRESSIVE"),
    },
    "moderate": {
        "preferred": ("MODERATE",),
        "acceptable": ("CONSERVATIVE", "MODERATE_AGGRESSIVE"),
        "exclude": ("VERY_AGGRESSIVE", "AGGRESSIVE"),
    },
    "aggressive": {
        "preferred": ("AGGRESSIVE", "MODERATE_AGGRESSIVE"),
        "acceptable": ("VERY_AGGRESSIVE", "MODERATE"),
        "exclude": (),
    },
}

RANKING_POLICY_DEFAULT = (
    "account_eligibility",
    "risk_compatibility",
    "user_requested_metric",
    "fee",
    "performance",
    "evidence_completeness",
)


def normalize_risk_label(label: Any) -> str:
    return str(label or "").replace(" ", "").replace("[", "").replace("]", "")


def bucket_from_record(record: dict[str, Any]) -> str | None:
    label = record.get("risk_label")
    if label:
        folded = normalize_risk_label(label)
        for key, bucket in LABEL_TO_BUCKET.items():
            if normalize_risk_label(key) == folded:
                return bucket
    grade = record.get("risk_grade")
    if isinstance(grade, int):
        return GRADE_TO_BUCKET.get(grade)
    return None


def label_for_grade(grade: Any) -> str | None:
    if isinstance(grade, int):
        return PROSPECTUS_GRADE_LABELS.get(grade)
    return None


def policy_for_tolerance(tolerance: str | None) -> dict[str, tuple[str, ...]] | None:
    if not tolerance:
        return None
    return RISK_TOLERANCE_POLICY.get(str(tolerance).lower())


def allowed_buckets(tolerance: str | None) -> tuple[str, ...]:
    policy = policy_for_tolerance(tolerance)
    if not policy:
        return ()
    return tuple(dict.fromkeys([*policy["preferred"], *policy["acceptable"]]))


def excluded_buckets(tolerance: str | None) -> tuple[str, ...]:
    policy = policy_for_tolerance(tolerance)
    if not policy:
        return ()
    return tuple(policy["exclude"])


def qualitative_risk_constraint_text(tolerance: str | None) -> str | None:
    if not tolerance or tolerance not in RISK_TOLERANCE_POLICY:
        return None
    return f"위험성향={tolerance}"


def record_matches_tolerance(record: dict[str, Any], tolerance: str | None) -> bool:
    if not tolerance:
        return True
    bucket = bucket_from_record(record)
    if bucket is None:
        return False
    return bucket in allowed_buckets(tolerance) and bucket not in excluded_buckets(tolerance)


def risk_match_score(record: dict[str, Any], tolerance: str | None) -> int:
    policy = policy_for_tolerance(tolerance)
    bucket = bucket_from_record(record)
    if not policy or not bucket:
        return 0
    if bucket in policy["preferred"]:
        return 2
    if bucket in policy["acceptable"]:
        return 1
    return 0


def evidence_coverage_score(record: dict[str, Any]) -> int:
    score = 0
    if record.get("risk_grade") is not None:
        score += 1
    if record.get("total_fee") is not None:
        score += 1
    if record.get("source_file"):
        score += 1
    if record.get("investment_risks"):
        score += 1
    performance = record.get("performance") or []
    if any((item.get("value_audit") or {}).get("status") == "VERIFIED" for item in performance):
        score += 1
    return score


def attach_ranking_breakdown(
    records: list[dict[str, Any]],
    *,
    tolerance: str | None,
    sort_by: str,
) -> list[dict[str, Any]]:
    fee_sorted = sorted(
        [item for item in records if isinstance(item.get("total_fee"), (int, float))],
        key=lambda item: item["total_fee"],
    )
    fee_rank = {id(item): index + 1 for index, item in enumerate(fee_sorted)}
    perf_sorted = sorted(
        [item for item in records if isinstance(item.get("selected_performance_value"), (int, float))],
        key=lambda item: item["selected_performance_value"],
        reverse=True,
    )
    perf_rank = {id(item): index + 1 for index, item in enumerate(perf_sorted)}
    for record in records:
        record["risk_bucket"] = bucket_from_record(record)
        record["ranking_breakdown"] = {
            "product_id": record.get("record_id"),
            "eligibility_score": 1 if record.get("irp_eligibility_status") == "ELIGIBLE" else 0,
            "risk_match": risk_match_score(record, tolerance),
            "fee_rank": fee_rank.get(id(record)),
            "performance_rank": perf_rank.get(id(record)),
            "evidence_coverage": evidence_coverage_score(record),
            "user_requested_metric": sort_by,
        }
    return records


def recommendation_sort_key(record: dict[str, Any], sort_by: str) -> tuple:
    breakdown = record.get("ranking_breakdown") or {}
    risk = -int(breakdown.get("risk_match") or 0)
    fee = record.get("total_fee")
    fee_key = float(fee) if isinstance(fee, (int, float)) else 10**9
    evidence = -int(breakdown.get("evidence_coverage") or 0)
    name = str(record.get("product_name") or "")
    if sort_by == "total_fee":
        return (fee_key, risk, evidence, name)
    if sort_by == "performance":
        perf = record.get("selected_performance_value")
        # Unverified scale values stay in the list but do not invent a converted rank.
        perf_key = -float(perf) if isinstance(perf, (int, float)) else 10**9
        return (perf_key, risk, fee_key, name)
    return (risk, fee_key, evidence, name)
