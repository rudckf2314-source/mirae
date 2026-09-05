from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def enum_value(value):
    return getattr(value, "value", value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit ProductExtraction v0.1 JSON batch quality")
    parser.add_argument("--input", default="data/standard_json", help="Directory with *.schema_v0.1.json")
    parser.add_argument("--output", default="data/cache/standard_batch_audit.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    input_dir = Path(args.input)
    if not input_dir.is_absolute():
        input_dir = root / input_dir
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output

    files = sorted(input_dir.glob("*.schema_v0.1.json"))
    if not files:
        print(f"No schema JSON files: {input_dir}")
        return 1

    status_counts: dict[str, Counter] = {}
    totals = Counter()
    orphan_class_refs: list[dict] = []
    missing_evidence_refs: list[dict] = []
    master_feeder_candidates = 0
    master_feeder_found = 0
    parse_errors: list[dict] = []

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            parse_errors.append({"file": path.name, "error": str(exc)})
            continue

        product = data.get("product") or {}
        if product.get("is_master_feeder") is True:
            master_feeder_candidates += 1
            if data.get("master_feeder_relations"):
                master_feeder_found += 1

        for field, value in (data.get("field_status") or {}).items():
            status_counts.setdefault(field, Counter())[str(enum_value(value))] += 1

        classes = {x.get("class_key") for x in data.get("classes", []) if x.get("class_key")}
        for collection in ("fees", "sales_charges", "performance"):
            for index, item in enumerate(data.get(collection, [])):
                key = item.get("class_key")
                if key is not None and key not in classes:
                    orphan_class_refs.append({"file": path.name, "collection": collection, "index": index, "class_key": key})

        evidence_ids = {x.get("evidence_id") for x in data.get("evidence", []) if x.get("evidence_id")}
        for collection in (
            "risk_ratings", "classes", "sales_charges", "fees", "master_feeder_relations",
            "hedging_policies", "performance", "capital_flows", "financial_metrics", "narratives",
        ):
            for index, item in enumerate(data.get(collection, [])):
                for evidence_id in item.get("evidence_ids", []) or []:
                    if evidence_id not in evidence_ids:
                        missing_evidence_refs.append({"file": path.name, "collection": collection, "index": index, "evidence_id": evidence_id})

        for key in ("classes", "fees", "sales_charges", "performance", "narratives", "evidence", "extraction_issues"):
            totals[key] += len(data.get(key, []) or [])

    report = {
        "files": len(files),
        "parse_errors": parse_errors,
        "field_status": {k: dict(v) for k, v in sorted(status_counts.items())},
        "master_feeder": {
            "is_master_feeder_true": master_feeder_candidates,
            "relations_found": master_feeder_found,
            "recovery_rate_pct": round(master_feeder_found / master_feeder_candidates * 100, 2) if master_feeder_candidates else None,
        },
        "totals": dict(totals),
        "orphan_class_references": orphan_class_refs,
        "missing_evidence_references": missing_evidence_refs,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved: {output}")
    return 0 if not parse_errors and not orphan_class_refs and not missing_evidence_refs else 2


if __name__ == "__main__":
    raise SystemExit(main())
