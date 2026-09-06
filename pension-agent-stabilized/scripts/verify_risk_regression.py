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

from parsers.pdf_parser import PdfParser  # noqa: E402
from processing.chunker import Chunker  # noqa: E402
from processing.narrative_extractor import apply_narrative_facts  # noqa: E402
from processing.risk_row_extractor import (  # noqa: E402
    collect_table_risk_candidates,
    compact_risk_text,
    is_container_risk_heading,
)
from processing.section_detector import SectionDetector  # noqa: E402
from schemas.product import CanonicalProduct, DocumentMeta  # noqa: E402


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _baseline_names(document_id: str) -> set[str]:
    path = ROOT / "data" / "cache" / "extracted" / f"{document_id}.json"
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        compact_risk_text(item.get("name"))
        for item in (payload.get("product") or {}).get("investment_risks") or []
        if item.get("name")
    }


def verify(document_ids: list[str]) -> dict:
    pdf_dir = ROOT / "data" / "cache" / "pdf"
    parser = PdfParser()
    detector = SectionDetector()
    chunker = Chunker()
    results: list[dict] = []
    for index, document_id in enumerate(document_ids, 1):
        pdf_path = next(iter(pdf_dir.glob(f"{document_id}*.pdf")), None)
        if pdf_path is None:
            results.append({"document_id": document_id, "ok": False, "reason": "PDF_NOT_FOUND"})
            continue
        baseline = _baseline_names(document_id)
        parsed = parser.parse(pdf_path, document_id=document_id)
        sections = detector.detect(parsed)
        chunks = chunker.chunk(parsed, sections, parsed.tables)
        candidates = collect_table_risk_candidates(chunks, parsed.tables)
        product = CanonicalProduct(
            document=DocumentMeta(
                document_id=document_id,
                document_hash=parsed.document_hash,
                file_name=pdf_path.name,
            )
        )
        # Match the production preflight path. Passing ParsedDocument enables
        # structure-aware table/block recovery instead of the legacy text-only
        # fallback used for unit-level calls.
        product = apply_narrative_facts(
            product, chunks, parsed.tables, parsed=parsed
        )
        names = {
            compact_risk_text(item.name)
            for item in product.product.investment_risks
            if item.name
        }
        chunk_map = {chunk.chunk_id: chunk.text or "" for chunk in chunks}
        generic = [
            item.name for item in product.product.investment_risks
            if is_container_risk_heading(item.name)
        ]
        unsupported = [
            item.name
            for item in product.product.investment_risks
            if compact_risk_text(item.name) not in compact_risk_text(
                " ".join(chunk_map.get(ref, "") for ref in item.evidence_refs)
            )
        ]
        baseline_retained = not baseline or len(names & baseline) / len(baseline) >= 0.5
        ok = bool(names) and not generic and not unsupported
        results.append(
            {
                "document_id": document_id,
                "ok": ok,
                "risk_count": len(names),
                "risk_names": sorted(names),
                "candidate_count": len(candidates),
                "generic_headings": generic,
                "unsupported_names": unsupported,
                "baseline_count": len(baseline),
                "baseline_names": sorted(baseline),
                "baseline_retained": baseline_retained,
                "baseline_changed": bool(baseline) and not baseline_retained,
            }
        )
        print(
            f"[{index}/{len(document_ids)}] {document_id} "
            f"risks={len(names)} candidates={len(candidates)} ok={ok}",
            flush=True,
        )
    return {
        "document_count": len(results),
        "passed": sum(bool(item.get("ok")) for item in results),
        "failed": sum(not bool(item.get("ok")) for item in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("problems", "controls", "all"), default="problems")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    all_rows = _rows(ROOT / "a" / "risk_template_audit_100.csv")
    problems = {
        row["document_id"]
        for row in _rows(ROOT / "a" / "risk_problem_pdfs_17.csv")
    }
    if args.scope == "problems":
        document_ids = [row["document_id"] for row in all_rows if row["document_id"] in problems]
    elif args.scope == "controls":
        controls = [row for row in all_rows if row["document_id"] not in problems]
        selected: list[dict[str, str]] = []
        quotas = {"세부구분형": 2, "혼합형": 8, "구분형": 10}
        for template, quota in quotas.items():
            matches = [
                row for row in controls
                if (
                    row["template_type"] == template
                    or row["template_type"].startswith(f"{template}(")
                )
                and row not in selected
            ]
            selected.extend(matches[:quota])
        document_ids = [row["document_id"] for row in selected[:20]]
    else:
        document_ids = [row["document_id"] for row in all_rows]
    if args.offset:
        document_ids = document_ids[args.offset :]
    if args.limit:
        document_ids = document_ids[:args.limit]

    report = verify(document_ids)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    output = args.output or (
        ROOT / "data" / "cache" / f"risk_regression_{args.scope}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
