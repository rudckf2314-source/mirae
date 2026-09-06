"""Generate pre-gold data quality audits from the product catalog."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from chatbot.display_units import format_financial_value
from chatbot.performance_audit import audit_performance_item
from chatbot.product_db_adapter import JsonProductDBAdapter, create_product_db_adapter
from chatbot.risk_policy import GRADE_TO_BUCKET, PROSPECTUS_GRADE_LABELS, RISK_TOLERANCE_POLICY


def main() -> None:
    out = REPO / "reports" / "pre_gold_baseline"
    out.mkdir(parents=True, exist_ok=True)
    adapter = create_product_db_adapter()
    rows: list[dict] = []
    unit_rows: list[dict] = []
    for record in adapter.records:
        for item in record.get("performance") or []:
            audit = audit_performance_item(item, record)
            rows.append({
                "product_name": record.get("product_name"),
                "class_name": record.get("class_name"),
                "source_file": record.get("source_file"),
                "metric": item.get("metric") or item.get("metric_type"),
                "period": item.get("period"),
                "raw_db_value": audit.get("raw_db_value"),
                "unit": audit.get("unit"),
                "status": audit.get("status"),
                "reason": audit.get("reason"),
                "source_text_excerpt": (audit.get("source_text_excerpt") or "").replace("\n", " ")[:200],
            })
        if record.get("total_fee") is not None:
            unit_rows.append({
                "product_name": record.get("product_name"),
                "field": "total_fee",
                "raw_value": record.get("total_fee"),
                "raw_unit": record.get("total_fee_unit"),
                "display_value": format_financial_value(record.get("total_fee"), record.get("total_fee_unit"), "fee"),
            })

    t008 = adapter.search("최근 1년 수익률이 높은 상품 5개 보여줘", limit=5)
    t008_audits = []
    for product in t008:
        t008_audits.append({
            "product_name": product.get("product_name"),
            "class_name": product.get("class_name"),
            "raw_value": product.get("selected_performance_value"),
            "unit": product.get("selected_performance_unit"),
            "status": product.get("selected_performance_status"),
            "audit": product.get("selected_performance_audit"),
        })

    sample = rows[:12]
    if t008_audits:
        sample = [
            {
                "product_name": item["product_name"],
                "class_name": item["class_name"],
                "source_file": None,
                "metric": "fund_return",
                "period": "1Y",
                "raw_db_value": item["raw_value"],
                "unit": item["unit"],
                "status": item["status"],
                "reason": (item.get("audit") or {}).get("reason"),
                "source_text_excerpt": ((item.get("audit") or {}).get("source_text_excerpt") or "").replace("\n", " ")[:200],
            }
            for item in t008_audits
        ] + [row for row in rows if row.get("status") == "VERIFIED"][:7]

    with (out / "performance_scale_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sample[0].keys()) if sample else ["status"])
        writer.writeheader()
        writer.writerows(sample)

    with (out / "unit_normalization_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["product_name", "field", "raw_value", "raw_unit", "display_value"])
        writer.writeheader()
        writer.writerows(unit_rows[:30])

    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[str(row.get("status"))] = status_counts.get(str(row.get("status")), 0) + 1

    grade_counts: dict[str, int] = {}
    for record in adapter.records:
        grade = record.get("risk_grade")
        label = record.get("risk_label")
        key = f"{grade}:{label}"
        grade_counts[key] = grade_counts.get(key, 0) + 1

    (out / "data_quality_audit.json").write_text(json.dumps({
        "record_count": len(adapter.records),
        "performance_status_counts": status_counts,
        "t008_top5": t008_audits,
        "risk_grade_label_counts": grade_counts,
        "notes": [
            "98776-class values match 펀드코드 tables, not returns. No arithmetic conversion was applied.",
            "Display layer formats PERCENT_PER_YEAR as 연 N% without changing stored values.",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    (out / "risk_mapping_policy.json").write_text(json.dumps({
        "source": "투자설명서 summary risk scale repeated across Standard JSON",
        "grade_labels": PROSPECTUS_GRADE_LABELS,
        "grade_to_bucket": GRADE_TO_BUCKET,
        "policy": RISK_TOLERANCE_POLICY,
        "expression": "candidate_filtering_not_suitability",
        "ranking_policy": [
            "account_eligibility",
            "risk_compatibility",
            "user_requested_metric",
            "fee",
            "performance",
            "evidence_completeness",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"records": len(adapter.records), "performance_rows": len(rows), "status_counts": status_counts, "t008_count": len(t008_audits)}
    (out / "_audit_run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
