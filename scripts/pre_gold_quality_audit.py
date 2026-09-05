"""Generate pre-gold baseline audit artifacts. Never invent or rescale values."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from chatbot.display_units import format_financial_value
from chatbot.performance_audit import annotate_performance, audit_performance_item
from chatbot.product_db_adapter import create_product_db_adapter
from chatbot.risk_policy import (
    GRADE_TO_BUCKET,
    PROSPECTUS_GRADE_LABELS,
    RISK_TOLERANCE_POLICY,
    RANKING_POLICY_DEFAULT,
)


OUT = REPO / "reports" / "pre_gold_baseline"


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    db = create_product_db_adapter()
    records = list(getattr(db, "records", []) or [])

    # --- performance scale audit ---
    perf_rows: list[dict] = []
    for record in records:
        annotated = annotate_performance(dict(record))
        for item in annotated.get("performance") or []:
            metric = item.get("metric_type") or item.get("metric")
            if str(metric or "").casefold() != "fund_return":
                continue
            if str(item.get("period") or "").upper() != "1Y":
                continue
            audit = item.get("value_audit") or audit_performance_item(item, annotated)
            perf_rows.append(
                {
                    "product_name": record.get("product_name"),
                    "class_name": record.get("class_name"),
                    "source_file": record.get("source_file"),
                    "raw_db_value": audit.get("raw_db_value"),
                    "unit": audit.get("unit"),
                    "metric_type": audit.get("metric_type"),
                    "period": audit.get("period"),
                    "status": audit.get("status"),
                    "reason": audit.get("reason"),
                    "display": format_financial_value(
                        audit.get("raw_db_value"),
                        audit.get("unit"),
                        "fund_return",
                        status=audit.get("status"),
                    ),
                    "source_text_excerpt": (audit.get("source_text_excerpt") or "")[:180],
                }
            )

    t008 = db.search("최근 1년 수익률이 높은 상품 5개 보여줘", 5)
    t008_rows = []
    for item in t008:
        audit = item.get("selected_performance_audit") or {}
        t008_rows.append(
            {
                "product_name": item.get("product_name"),
                "class_name": item.get("class_name"),
                "raw_db_value": item.get("selected_performance_value"),
                "unit": item.get("selected_performance_unit") or audit.get("unit"),
                "status": item.get("selected_performance_status") or audit.get("status"),
                "reason": audit.get("reason"),
                "display": format_financial_value(
                    item.get("selected_performance_value"),
                    item.get("selected_performance_unit") or audit.get("unit"),
                    "fund_return",
                    status=item.get("selected_performance_status") or audit.get("status"),
                ),
            }
        )

    with (OUT / "performance_scale_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "product_name",
                "class_name",
                "source_file",
                "raw_db_value",
                "unit",
                "metric_type",
                "period",
                "status",
                "reason",
                "display",
                "source_text_excerpt",
            ],
        )
        writer.writeheader()
        # Prefer anomalous samples first, then T008 set, then a verified sample.
        ordered = sorted(
            perf_rows,
            key=lambda row: (
                0 if row["status"] in {"SOURCE_CONFLICT", "SCALE_MISMATCH"} else 1,
                abs(float(row["raw_db_value"] or 0)),
            ),
            reverse=True,
        )
        for row in ordered[:40]:
            writer.writerow(row)

    # --- unit normalization audit ---
    unit_rows = [
        {"raw_value": 0.71, "raw_unit": "PERCENT_PER_YEAR", "metric": "fee", "display": format_financial_value(0.71, "PERCENT_PER_YEAR", "fee")},
        {"raw_value": 1.57, "raw_unit": "PERCENT_PER_YEAR", "metric": "fee", "display": format_financial_value(1.57, "PERCENT_PER_YEAR", "fee")},
        {"raw_value": 3.2, "raw_unit": "PERCENT", "metric": "fund_return", "display": format_financial_value(3.2, "PERCENT", "fund_return", status="VERIFIED")},
        {"raw_value": 98776.0, "raw_unit": "PERCENT", "metric": "fund_return", "display": format_financial_value(98776.0, "PERCENT", "fund_return", status="SOURCE_CONFLICT")},
        {"raw_value": 100, "raw_unit": "BASIS_POINT", "metric": "spread", "display": format_financial_value(100, "BASIS_POINT")},
        {"raw_value": 1000000, "raw_unit": "KRW", "metric": "amount", "display": format_financial_value(1000000, "KRW")},
    ]
    with (OUT / "unit_normalization_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["raw_value", "raw_unit", "metric", "display"])
        writer.writeheader()
        writer.writerows(unit_rows)

    # --- risk mapping policy ---
    write_json(
        OUT / "risk_mapping_policy.json",
        {
            "note": "Filter policy only. No enterprise suitability rule was found in provided docs.",
            "prospectus_grade_labels": PROSPECTUS_GRADE_LABELS,
            "grade_to_bucket": GRADE_TO_BUCKET,
            "risk_tolerance_policy": RISK_TOLERANCE_POLICY,
            "ranking_policy_default": list(RANKING_POLICY_DEFAULT),
            "expression_guidance": "입력한 위험 선호 조건 기준으로 비교 가능한 후보",
        },
    )

    status_counts: dict[str, int] = {}
    for row in perf_rows:
        status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1

    write_json(
        OUT / "data_quality_audit.json",
        {
            "product_record_count": len(records),
            "fund_return_1y_count": len(perf_rows),
            "performance_status_counts": status_counts,
            "t008_top5": t008_rows,
            "unit_samples": unit_rows,
            "notes": [
                "No arithmetic rescale is applied to performance values.",
                "SOURCE_CONFLICT often indicates fund-code table contamination in extraction.",
                "Display layer formats units; raw DB values remain unchanged.",
            ],
        },
    )

    (OUT / "known_limitations.md").write_text(
        """# Known limitations (pre Gold Set)

- TDF catalog names are aliased to 라이프사이클 products; literal `TDF` product names are not in the provided catalog.
- Some `fund_return` / `1Y` Standard JSON values are contaminated by fund-code tables (e.g. 98776.0). These are marked `SOURCE_CONFLICT` / scale-audit status and are not rescale-corrected by the LLM.
- Enterprise RAG may be absent for some product-fact answers when PostgreSQL authoritative rows are sufficient.
- Product class coverage is limited to classes present in Standard JSON / Postgres; not every share class has complete fee/performance/PDF linkage.
- Recommendation ranking is deterministic candidate filtering, not a regulated suitability engine. No enterprise suitability matrix was found in the provided docs.
- HyperCLOVA product narrative generation may fail; product-fact answers fall back to DB composer.
- Performance magnitude verification depends on attached evidence text quality.
""".strip()
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps({"records": len(records), "fund_return_1y": len(perf_rows), "status_counts": status_counts, "t008": len(t008_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
