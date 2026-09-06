"""Preflight Standard JSON integrity for the frozen 100-doc baseline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.safety import persistence_blockers  # noqa: E402
from schemas.product_schema import ProductExtraction  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standard-dir", type=Path, default=ROOT / "data/standard_json")
    parser.add_argument("--extracted-dir", type=Path, default=ROOT / "data/cache/extracted")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data/cache/standard_json_preflight.json",
    )
    args = parser.parse_args()

    baseline_ids = {path.stem for path in args.extracted_dir.glob("*.json")}
    docs = []
    parse_errors = []
    blockers = []
    orphan_refs = []
    broken_evidence = []
    ids: list[str] = []
    hashes: list[str | None] = []
    evidence_ids: list[str] = []
    risk_narrative_total = 0

    for path in sorted(args.standard_dir.glob("*.schema_v0.1.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            product = ProductExtraction.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append({"file": path.name, "error": str(exc)})
            continue
        document_id = product.source_document.document_id
        if document_id not in baseline_ids:
            continue
        ids.append(document_id)
        hashes.append(product.source_document.file_hash)
        class_keys = {item.class_key for item in product.classes}
        evid = {item.evidence_id for item in product.evidence}
        evidence_ids.extend(evid)
        risk_narrative_total += sum(
            1
            for item in product.narratives
            if getattr(item.narrative_type, "value", item.narrative_type) == "INVESTMENT_RISK"
        )
        for fee in product.fees:
            if fee.class_key and fee.class_key not in class_keys:
                orphan_refs.append({"document_id": document_id, "kind": "fee", "class_key": fee.class_key})
        for charge in product.sales_charges:
            if charge.class_key and charge.class_key not in class_keys:
                orphan_refs.append(
                    {"document_id": document_id, "kind": "sales_charge", "class_key": charge.class_key}
                )
        for row in product.performance:
            if row.class_key and row.class_key not in class_keys:
                orphan_refs.append(
                    {"document_id": document_id, "kind": "performance", "class_key": row.class_key}
                )
        for narrative in product.narratives:
            for evidence_id in narrative.evidence_ids or []:
                if evidence_id not in evid:
                    broken_evidence.append(
                        {
                            "document_id": document_id,
                            "narrative_type": str(narrative.narrative_type),
                            "evidence_id": evidence_id,
                        }
                    )
        blocked = persistence_blockers(product)
        if blocked:
            blockers.append({"document_id": document_id, "blockers": blocked})
        docs.append(
            {
                "document_id": document_id,
                "file": path.name,
                "schema_version": product.schema_version,
                "file_hash": product.source_document.file_hash,
                "counts": {
                    "risk_ratings": len(product.risk_ratings),
                    "classes": len(product.classes),
                    "fees": len(product.fees),
                    "sales_charges": len(product.sales_charges),
                    "performance": len(product.performance),
                    "narratives": len(product.narratives),
                    "investment_risk_narratives": sum(
                        1
                        for item in product.narratives
                        if getattr(item.narrative_type, "value", item.narrative_type)
                        == "INVESTMENT_RISK"
                    ),
                    "evidence": len(product.evidence),
                    "field_status": len(product.field_status),
                    "extraction_issues": len(product.extraction_issues),
                },
            }
        )

    id_counts = Counter(ids)
    hash_counts = Counter(h for h in hashes if h)
    evidence_counts = Counter(evidence_ids)
    summary = {
        "baseline_docs": len(docs),
        "parse_errors": parse_errors,
        "duplicate_document_ids": [doc for doc, n in id_counts.items() if n > 1],
        "duplicate_file_hash_groups": [
            {"file_hash": digest, "count": count}
            for digest, count in hash_counts.items()
            if count > 1
        ],
        "orphan_class_references": orphan_refs,
        "broken_narrative_evidence_refs": broken_evidence,
        "duplicate_evidence_ids_global": [
            eid for eid, n in evidence_counts.items() if n > 1
        ],
        "persistence_blockers": blockers,
        "investment_risk_narrative_total": risk_narrative_total,
        "ok": not parse_errors
        and not orphan_refs
        and not broken_evidence
        and not blockers
        and not any(n > 1 for n in id_counts.values())
        and not any(n > 1 for n in evidence_counts.values()),
        "documents": docs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(args.out),
                "baseline_docs": summary["baseline_docs"],
                "ok": summary["ok"],
                "parse_errors": len(parse_errors),
                "orphan_class_references": len(orphan_refs),
                "broken_narrative_evidence_refs": len(broken_evidence),
                "persistence_blockers": len(blockers),
                "duplicate_file_hash_groups": len(summary["duplicate_file_hash_groups"]),
                "investment_risk_narrative_total": risk_narrative_total,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
