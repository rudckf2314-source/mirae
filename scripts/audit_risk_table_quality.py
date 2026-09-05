"""Audit canonical risks vs validated table candidates after extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from processing.chunker import Chunker  # noqa: E402
from processing.risk_heading_anchor import is_generic_risk_heading  # noqa: E402
from processing.risk_row_extractor import is_container_risk_heading  # noqa: E402
from processing.risk_table_selection import (  # noqa: E402
    assess_table_risk_confidence,
    name_key,
)
from processing.section_detector import SectionDetector  # noqa: E402
from schemas.document import ParsedDocument  # noqa: E402
from schemas.product import CanonicalProduct  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted-dir", type=Path, default=ROOT / "data/cache/extracted")
    parser.add_argument("--parsed-dir", type=Path, default=ROOT / "data/cache/parsed")
    parser.add_argument("--out", type=Path, default=ROOT / "data/cache/risk_table_quality_audit.json")
    args = parser.parse_args()

    docs = []
    totals = {"tp": 0, "fn": 0, "fp": 0, "canonical": 0, "validated_table": 0}
    for path in sorted(args.extracted_dir.glob("*.json")):
        parsed_path = args.parsed_dir / path.name
        if not parsed_path.exists():
            continue
        product = CanonicalProduct.model_validate_json(path.read_text(encoding="utf-8"))
        parsed = ParsedDocument.model_validate_json(parsed_path.read_text(encoding="utf-8"))
        sections = SectionDetector().detect(parsed)
        chunks = Chunker().chunk(parsed, sections, tables=parsed.tables)
        assessment = assess_table_risk_confidence(chunks, parsed.tables)
        table_keys = {item.name_key for item in assessment.valid}
        canon_keys = {
            name_key(item.name)
            for item in product.product.investment_risks
            if item.name
            and not is_generic_risk_heading(item.name)
            and not is_container_risk_heading(item.name)
        }
        matched = table_keys & canon_keys
        table_only = table_keys - canon_keys
        canon_only = canon_keys - table_keys
        tp, fn, fp = len(matched), len(table_only), len(canon_only)
        totals["tp"] += tp
        totals["fn"] += fn
        totals["fp"] += fp
        totals["canonical"] += len(canon_keys)
        totals["validated_table"] += len(table_keys)
        docs.append(
            {
                "document_id": path.stem,
                "confidence": assessment.level,
                "table_candidate_count": len(table_keys),
                "canonical_risk_count": len(canon_keys),
                "matched": tp,
                "table_only_fn": sorted(table_only),
                "canonical_only_fp": sorted(canon_only),
            }
        )

    precision = totals["tp"] / max(totals["tp"] + totals["fp"], 1)
    recall = totals["tp"] / max(totals["tp"] + totals["fn"], 1)
    f1 = (2 * precision * recall / max(precision + recall, 1e-9)) if (precision + recall) else 0.0
    report = {
        "documents": len(docs),
        "totals": totals,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "high_fn_docs": sorted(
            (d for d in docs if len(d["table_only_fn"]) >= 3),
            key=lambda d: -len(d["table_only_fn"]),
        )[:20],
        "docs": docs,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(args.out),
                "documents": report["documents"],
                "precision": report["precision"],
                "recall": report["recall"],
                "f1": report["f1"],
                "canonical_total": totals["canonical"],
                "validated_table_total": totals["validated_table"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
