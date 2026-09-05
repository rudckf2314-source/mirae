from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from schemas.product_schema import ProductExtraction  # noqa: E402


def compact(value: object) -> str:
    text = re.sub(r"(?:주식회사|㈜|\(주\))", "", str(value or ""))
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text).lower()


def supported(value: object, texts: list[str]) -> bool:
    needle = compact(value)
    if not needle:
        return False
    sources = [compact(text) for text in texts]
    if any(needle in source for source in sources):
        return True
    joined = "".join(sources)
    if needle in joined:
        return True
    # Long narrative text can differ in whitespace/OCR punctuation. Require that
    # almost all source tokens occur in one linked evidence span.
    tokens = [compact(token) for token in re.findall(r"[가-힣A-Za-z0-9]+", str(value))]
    tokens = [token for token in tokens if len(token) >= 2]
    return bool(tokens) and (
        any(sum(token in source for token in tokens) / len(tokens) >= 0.9 for source in sources)
        or sum(token in joined for token in tokens) / len(tokens) >= 0.9
    )


def pdf_pages(path: Path) -> list[str]:
    with fitz.open(path) as doc:
        return [page.get_text("text") for page in doc]


def source_on_pdf(source: str, page_text: str) -> bool:
    source_compact = compact(source)
    page_compact = compact(page_text)
    if source_compact and source_compact in page_compact:
        return True
    # Reconstructed tables add Markdown syntax/TABLE_ID and may reorder cells.
    # Verify their lexical content against the actual page instead of requiring
    # an impossible byte-for-byte substring match.
    cleaned = re.sub(r"TABLE_ID:\s*\S+|page=\d+|section=\w+|[-|]+", " ", source)
    tokens = {
        compact(token)
        for token in re.findall(r"[가-힣A-Za-z0-9.%()\-]+", cleaned)
        if len(compact(token)) >= 2
    }
    if not tokens:
        return False
    return sum(token in page_compact for token in tokens) / len(tokens) >= 0.85


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "cache")
    parser.add_argument("--extracted-dir", type=Path, default=ROOT / "data/cache/extracted")
    parser.add_argument("--schema-dir", type=Path, default=ROOT / "data/standard_json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/cache/pdf_json_alignment_audit.json")
    args = parser.parse_args()

    documents = []
    totals = Counter()
    for extracted_path in sorted(args.extracted_dir.glob("*.json")):
        stem = extracted_path.stem
        pdf_path = args.pdf_dir / f"{stem}.pdf"
        schema_path = args.schema_dir / f"{stem}.schema_v0.1.json"
        row = {"document_id": stem, "pdf": pdf_path.exists(), "schema": schema_path.exists(), "issues": []}
        if not pdf_path.exists():
            row["issues"].append("PDF_MISSING")
            documents.append(row)
            continue

        canonical = json.loads(extracted_path.read_text(encoding="utf-8"))
        pages = pdf_pages(pdf_path)
        evidence = {item["chunk_id"]: item for item in canonical.get("evidence", [])}
        evidence_pass = 0
        evidence_failed = []
        for item in evidence.values():
            page = int(item.get("page_start") or 0)
            page_end = int(item.get("page_end") or page)
            source = item.get("source_text") or ""
            page_text = "\n".join(pages[page - 1 : min(page_end, len(pages))])
            ok = 1 <= page <= len(pages) and source_on_pdf(source, page_text)
            evidence_pass += int(ok)
            if not ok:
                evidence_failed.append({
                    "evidence_id": item.get("chunk_id"),
                    "page_start": page,
                    "page_end": page_end,
                    "section_type": item.get("section_type"),
                    "source_preview": source[:240],
                })
        row["evidence_pdf"] = {
            "pass": evidence_pass,
            "total": len(evidence),
            "failed": evidence_failed,
        }
        totals["evidence_pdf_pass"] += evidence_pass
        totals["evidence_pdf_total"] += len(evidence)

        checks: list[tuple[str, object, list[str]]] = []
        product = canonical.get("product", {})
        all_evidence_texts = [item.get("source_text") or "" for item in evidence.values()]
        for field in ("name", "manager", "fund_code"):
            if product.get(field):
                checks.append((f"product.{field}", product[field], all_evidence_texts))
        risk_rating = product.get("risk") or {}
        risk_texts = [evidence[r]["source_text"] for r in risk_rating.get("evidence_refs", []) if r in evidence]
        for field in ("grade", "label"):
            if risk_rating.get(field) is not None:
                checks.append((f"product.risk.{field}", risk_rating[field], risk_texts))
        for field in ("investment_objective", "investment_strategy"):
            item = product.get(field) or {}
            texts = [evidence[r]["source_text"] for r in item.get("evidence_refs", []) if r in evidence]
            if item.get("text"):
                checks.append((f"product.{field}", item["text"], texts))
        for index, item in enumerate(product.get("investment_risks", [])):
            texts = [evidence[r]["source_text"] for r in item.get("evidence_refs", []) if r in evidence]
            if item.get("name"):
                checks.append((f"risk[{index}].name", item["name"], texts))
        for group in ("classes", "fees", "performance"):
            for index, item in enumerate(canonical.get(group, [])):
                texts = [evidence[r]["source_text"] for r in item.get("evidence_refs", []) if r in evidence]
                value = item.get("class_name")
                if value:
                    checks.append((f"{group}[{index}].class_name", value, texts))

        failed = [path for path, value, texts in checks if not supported(value, texts)]
        row["value_evidence"] = {"pass": len(checks) - len(failed), "total": len(checks), "failed": failed}
        totals["value_evidence_pass"] += len(checks) - len(failed)
        totals["value_evidence_total"] += len(checks)

        if schema_path.exists():
            try:
                schema = ProductExtraction.model_validate_json(schema_path.read_text(encoding="utf-8"))
                row["schema_validation"] = "PASS"
                dumped = schema.model_dump(mode="json")
                evidence_ids = {item["evidence_id"] for item in dumped["evidence"]}
                refs = []
                for group in ("risk_ratings", "classes", "sales_charges", "fees", "master_feeder_relations", "performance", "financial_metrics", "narratives"):
                    for item in dumped[group]:
                        refs.extend(item.get("evidence_ids", []))
                missing_refs = sorted(set(refs) - evidence_ids)
                class_keys = {item["class_key"] for item in dumped["classes"]}
                used_keys = {item["class_key"] for group in ("sales_charges", "fees", "performance") for item in dumped[group] if item.get("class_key")}
                row["schema_integrity"] = {
                    "missing_evidence_refs": len(missing_refs),
                    "duplicate_evidence_ids": len(dumped["evidence"]) - len(evidence_ids),
                    "orphan_class_keys": sorted(used_keys - class_keys),
                }
                totals["schema_valid"] += 1
                totals["schema_missing_refs"] += len(missing_refs)
                totals["schema_duplicate_evidence"] += len(dumped["evidence"]) - len(evidence_ids)
                totals["schema_orphan_class_keys"] += len(used_keys - class_keys)
            except Exception as exc:
                row["schema_validation"] = "FAIL"
                row["issues"].append(f"SCHEMA_VALIDATION: {exc}")
        else:
            row["schema_validation"] = "MISSING"
            row["issues"].append("SCHEMA_MISSING")
        documents.append(row)

    def percent(passed: int, total: int) -> float | None:
        return round(passed * 100 / total, 2) if total else None

    report = {
        "summary": {
            "documents": len(documents),
            "schemas_present": sum(row["schema"] for row in documents),
            "schema_validation_pass": totals["schema_valid"],
            "evidence_pdf_match_pct": percent(totals["evidence_pdf_pass"], totals["evidence_pdf_total"]),
            "evidence_pdf_pass": totals["evidence_pdf_pass"],
            "evidence_pdf_total": totals["evidence_pdf_total"],
            "value_evidence_match_pct": percent(totals["value_evidence_pass"], totals["value_evidence_total"]),
            "value_evidence_pass": totals["value_evidence_pass"],
            "value_evidence_total": totals["value_evidence_total"],
            "missing_evidence_refs": totals["schema_missing_refs"],
            "duplicate_evidence_ids": totals["schema_duplicate_evidence"],
            "orphan_class_keys": totals["schema_orphan_class_keys"],
        },
        "documents": documents,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
