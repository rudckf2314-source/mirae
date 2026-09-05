from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


GENERIC_RISK_HEADINGS = {
    "투자위험",
    "주요투자위험",
    "집합투자기구의투자위험",
    "투자위험의주요내용",
}


def _risk_table_documents(manifest: Path | None) -> set[str]:
    if manifest is None or not manifest.exists():
        return set()
    with manifest.open(encoding="utf-8-sig", newline="") as handle:
        return {
            str(row.get("document_id") or "").strip()
            for row in csv.DictReader(handle)
            if str(row.get("document_id") or "").strip()
        }


def audit(directory: Path, risk_manifest: Path | None = None) -> dict:
    clusters: Counter[str] = Counter()
    documents: list[dict] = []
    risk_table_documents = _risk_table_documents(risk_manifest)
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        extraction = payload.get("extraction") or {}
        product = payload.get("product") or {}
        document = payload.get("document") or {}
        warnings = extraction.get("warnings") or []
        verification = extraction.get("verification") or {}
        document_id = document.get("document_id") or path.stem
        local: Counter[str] = Counter()

        for outcome in extraction.get("ownership") or []:
            owner = outcome.get("owner")
            field = outcome.get("field")
            status = str(outcome.get("status") or "").lower()
            if owner == "table":
                local[f"table_section_{status}"] += 1
            elif owner == "metadata" and status != "valid":
                local[f"metadata_{field}_{status}"] += 1

        for outcome in extraction.get("candidate_outcomes") or []:
            owner = outcome.get("owner")
            field = outcome.get("field")
            status = str(outcome.get("status") or "").lower()
            if owner == "table":
                local[f"table_gate_{status}"] += 1
            elif field == "investment_risks":
                local[f"risk_candidate_{status}"] += 1
            elif field == "classes":
                local[f"class_candidate_{status}"] += 1
            elif field in {"investment_objective", "investment_strategy"}:
                local[f"narrative_candidate_{status}"] += 1

        if not document.get("as_of_date"):
            local["metadata_missing_date"] += 1
        if not product.get("fund_code"):
            local["metadata_missing_fund_code"] += 1

        objective = ((product.get("investment_objective") or {}).get("text") or "")
        strategy = ((product.get("investment_strategy") or {}).get("text") or "")
        if objective and strategy and _compact(objective) == _compact(strategy):
            local["narrative_duplicate"] += 1
        if any("truncated" in warning.lower() for warning in warnings):
            local["narrative_truncated"] += 1
        local["risk_description_missing"] += sum(
            1
            for risk in product.get("investment_risks") or []
            if risk.get("name") and not risk.get("description")
        )
        risks = product.get("investment_risks") or []
        if document_id in risk_table_documents and not risks:
            local["risk_table_present_empty"] += 1
        evidence = {
            item.get("chunk_id"): item.get("source_text") or item.get("table_markdown") or ""
            for item in payload.get("evidence") or []
            if item.get("chunk_id")
        }
        for risk in risks:
            name = _compact(risk.get("name") or "")
            refs = risk.get("evidence_refs") or []
            if name in GENERIC_RISK_HEADINGS:
                local["risk_generic_heading"] += 1
            if not refs or any(ref not in evidence for ref in refs):
                local["risk_evidence_invalid"] += 1
                continue
            source = _compact(" ".join(evidence[ref] for ref in refs))
            if name and name not in source:
                local["risk_name_not_source_anchored"] += 1
        local["ownership_invariant_fail"] += sum(
            1 for warning in warnings if warning.startswith("ownership_invariant:")
        )
        local["verification_fail"] += int(verification.get("fail_count") or 0)
        local["verification_warning"] += int(verification.get("warning_count") or 0)

        clusters.update(local)
        documents.append(
            {
                "document_id": document_id,
                "status": extraction.get("status"),
                "clusters": dict(local),
            }
        )
    return {
        "document_count": len(documents),
        "clusters": dict(sorted(clusters.items())),
        "documents": documents,
    }


def _compact(text: str) -> str:
    return "".join(text.split())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", type=Path, default=Path("data/cache/extracted"))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--risk-manifest",
        type=Path,
        default=Path("a/risk_template_audit_100.csv"),
    )
    args = parser.parse_args()
    report = audit(args.directory, args.risk_manifest)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
