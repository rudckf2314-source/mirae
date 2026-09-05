from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import Settings, get_settings
from services.extraction_service import ExtractionService
from workflows.checkpoint_store import NodeCheckpointStore


def affected_ids(audit: dict) -> list[str]:
    selected: list[str] = []
    narrative_nulls = {
        "product.investment_objective.text",
        "product.investment_strategy.text",
    }
    for row in audit.get("documents", []):
        if (
            narrative_nulls.intersection(row.get("core_nulls", []))
            or row.get("strategy_contamination")
            or row.get("risk_name_newlines")
            or row.get("bad_risk_headings")
            or row.get("numeric_evidence_failures")
        ):
            selected.append(row["document_id"])
    return sorted(set(selected))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=ROOT / "data/cache/pdf_json_direct_audit.json")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "data/cache/pdf")
    parser.add_argument("--stage-dir", type=Path, default=ROOT / "data/cache/stage_v3")
    parser.add_argument("--ids-csv", type=Path)
    args = parser.parse_args()

    if args.ids_csv:
        with args.ids_csv.open(encoding="utf-8-sig", newline="") as handle:
            ids = [row["document_id"] for row in csv.DictReader(handle)]
    else:
        audit = json.loads(args.audit.read_text(encoding="utf-8"))
        ids = affected_ids(audit)
    base = get_settings()
    settings = Settings(**{
        **base.model_dump(),
        "cache_dir": args.stage_dir,
        "standard_json_dir": args.stage_dir / "standard_json",
        "db_auto_save": False,
        "database_url": "",
    })
    service = ExtractionService(settings=settings)
    # Parsing/chunking and deterministic chain checkpoints are immutable and hash-keyed.
    service.node_checkpoints = NodeCheckpointStore(ROOT / "data/cache/node_checkpoints")

    results: list[dict] = []
    for index, document_id in enumerate(ids, 1):
        pdf = args.pdf_dir / f"{document_id}.pdf"
        print(f"[{index}/{len(ids)}] START {document_id}", flush=True)
        try:
            result = service.process_pdf(pdf, file_name=pdf.name, force=True)
            product = result.product
            results.append({
                "document_id": document_id,
                "ok": product is not None and not result.error,
                "error": result.error,
                "objective": bool(product and product.product.investment_objective.text),
                "strategy": bool(product and product.product.investment_strategy.text),
                "verification": product.extraction.verification.status if product else None,
            })
            print(f"[{index}/{len(ids)}] DONE {document_id}", flush=True)
        except Exception as exc:
            results.append({"document_id": document_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{index}/{len(ids)}] FAILED {document_id}: {type(exc).__name__}: {exc}", flush=True)

    report = {
        "total": len(ids),
        "passed": sum(bool(item["ok"]) for item in results),
        "failed": sum(not item["ok"] for item in results),
        "results": results,
    }
    args.stage_dir.mkdir(parents=True, exist_ok=True)
    (args.stage_dir / "reextract_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in ("total", "passed", "failed")}, ensure_ascii=False), flush=True)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
