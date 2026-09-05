from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
CORE_TEXT_FIELDS = ("name", "manager", "fund_code")


def compact(value: object) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def tokens(value: object) -> list[str]:
    return [compact(item) for item in re.findall(r"[\w.%()\-]+", str(value or "")) if len(compact(item)) >= 2]


def supported(value: object, sources: list[str], threshold: float = 0.85) -> bool:
    needle = compact(value)
    haystacks = [compact(source) for source in sources]
    if not needle or not haystacks:
        return False
    if any(needle in source for source in haystacks) or needle in "".join(haystacks):
        return True
    parts = tokens(value)
    joined = "".join(haystacks)
    return bool(parts) and sum(part in joined for part in parts) / len(parts) >= threshold


def evidence_supported(source: str, page_text: str) -> bool:
    if supported(source, [page_text]):
        return True
    source_tokens = set(tokens(re.sub(r"TABLE_ID:\s*\S+|page=\d+|section=\w+", " ", source)))
    page = compact(page_text)
    return bool(source_tokens) and sum(token in page for token in source_tokens) / len(source_tokens) >= 0.75


def numeric_supported(value: object, sources: list[str]) -> bool:
    raw = str(value or "").strip().replace(",", "").rstrip("%")
    if not raw:
        return True
    try:
        expected = float(raw)
    except ValueError:
        return supported(value, sources, threshold=0.75)
    values: list[float] = []
    for source in sources:
        # A shortened inception date such as 16.03.16 must never support 16.03.
        without_dates = re.sub(r"(?<!\d)\d{2,4}[./-]\d{1,2}[./-]\d{1,2}(?!\d)", " ", source)
        for token in re.findall(r"(?<![\d.])-?\d[\d,]*(?:\.\d+)?%?(?![\d.])", without_dates):
            cleaned = token.rstrip("%")
            try:
                values.append(float(cleaned.replace(",", "")))
            except ValueError:
                continue
            # Some Korean fee tables print a decimal comma (1,807 means 1.807%).
            if cleaned.count(",") == 1 and "." not in cleaned:
                integer, fraction = cleaned.split(",", 1)
                if 1 <= len(fraction) <= 4:
                    try:
                        values.append(float(f"{integer}.{fraction}"))
                    except ValueError:
                        pass
    return any(abs(item - expected) <= 1e-9 for item in values)


def all_evidence_refs(payload: object, *, key: str = "") -> list[str]:
    refs: list[str] = []
    if isinstance(payload, dict):
        for child_key, value in payload.items():
            if child_key == "evidence_refs" and isinstance(value, list):
                refs.extend(str(item) for item in value)
            elif child_key not in {"evidence", "extraction"}:
                refs.extend(all_evidence_refs(value, key=child_key))
    elif isinstance(payload, list):
        for value in payload:
            refs.extend(all_evidence_refs(value, key=key))
    return refs


def fact_view(payload: object) -> object:
    """Remove document/run provenance while retaining extracted business facts."""
    if isinstance(payload, dict):
        return {
            key: fact_view(value)
            for key, value in sorted(payload.items())
            if key not in {"evidence_refs", "evidence", "extraction", "document"}
        }
    if isinstance(payload, list):
        values = [fact_view(value) for value in payload]
        return sorted(values, key=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True))
    if isinstance(payload, str):
        return re.sub(r"\s+", "", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / "data/cache/pdf")
    parser.add_argument("--extracted-dir", type=Path, default=ROOT / "data/cache/extracted")
    parser.add_argument("--output", type=Path, default=ROOT / "data/cache/pdf_json_direct_audit.json")
    args = parser.parse_args()

    pdfs = {path.stem: path for path in args.pdf_dir.glob("*.pdf")}
    jsons = {path.stem: path for path in args.extracted_dir.glob("*.json")}
    totals: Counter[str] = Counter()
    rows: list[dict] = []
    hash_groups: dict[str, list[tuple[str, dict]]] = defaultdict(list)

    for stem in sorted(set(pdfs) | set(jsons)):
        pdf_path, json_path = pdfs.get(stem), jsons.get(stem)
        row: dict = {"document_id": stem, "issues": []}
        if pdf_path is None or json_path is None:
            row["issues"].append("PDF_MISSING" if pdf_path is None else "JSON_MISSING")
            rows.append(row)
            continue

        raw = json_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        document = payload.get("document") or {}
        product = payload.get("product") or {}
        evidence = {item.get("chunk_id"): item for item in payload.get("evidence") or [] if item.get("chunk_id")}
        actual_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        expected_hash = document.get("document_hash")
        hash_groups[actual_hash].append((stem, payload))
        with pymupdf.open(pdf_path) as pdf:
            pages = [page.get_text("text") for page in pdf]
        numeric_pages: set[int] = set()
        for group in ("fees", "performance", "aum"):
            for fact in payload.get(group) or []:
                if not compact(fact.get("raw_cell_text")):
                    continue
                for ref in fact.get("evidence_refs") or []:
                    item_evidence = evidence.get(ref)
                    if not item_evidence:
                        continue
                    start = int(item_evidence.get("page_start") or 0)
                    end = int(item_evidence.get("page_end") or start)
                    numeric_pages.update(range(max(1, start), min(end, len(pages)) + 1))
        direct_pages = list(pages)
        with pdfplumber.open(pdf_path) as pdf:
            for page_number in numeric_pages:
                direct_pages[page_number - 1] += "\n" + (pdf.pages[page_number - 1].extract_text() or "")

        row["hash_match"] = actual_hash == expected_hash
        row["page_count_match"] = len(pages) == document.get("page_count")
        row["replacement_characters"] = raw.count("�")
        if not row["hash_match"]:
            row["issues"].append("DOCUMENT_HASH_MISMATCH")
        if not row["page_count_match"]:
            row["issues"].append("PAGE_COUNT_MISMATCH")
        if row["replacement_characters"]:
            row["issues"].append("TEXT_ENCODING_CORRUPTION")

        refs = all_evidence_refs(payload)
        missing_refs = sorted(set(refs) - set(evidence))
        row["missing_evidence_refs"] = missing_refs
        if missing_refs:
            row["issues"].append("MISSING_EVIDENCE_REFS")

        evidence_failures = []
        for evidence_id, item in evidence.items():
            start = int(item.get("page_start") or 0)
            end = int(item.get("page_end") or start)
            page_text = "\n".join(direct_pages[max(0, start - 1):min(end, len(direct_pages))])
            if not (1 <= start <= end <= len(pages)) or not evidence_supported(item.get("source_text") or "", page_text):
                evidence_failures.append(evidence_id)
        row["evidence_pdf"] = {"pass": len(evidence) - len(evidence_failures), "total": len(evidence), "failed": evidence_failures}
        if evidence_failures:
            row["issues"].append("EVIDENCE_PDF_MISMATCH")

        core_failures = []
        core_nulls = []
        all_sources = [item.get("source_text") or "" for item in evidence.values()]
        for field in CORE_TEXT_FIELDS:
            value = product.get(field)
            if value:
                if not supported(value, all_sources):
                    core_failures.append(f"product.{field}")
            else:
                core_nulls.append(f"product.{field}")
        for field in ("investment_objective", "investment_strategy"):
            item = product.get(field) or {}
            value = item.get("text")
            path = f"product.{field}.text"
            if not value:
                core_nulls.append(path)
                continue
            sources = [evidence[ref].get("source_text") or "" for ref in item.get("evidence_refs") or [] if ref in evidence]
            if not supported(value, sources):
                core_failures.append(path)
        row["core_nulls"] = core_nulls
        row["core_evidence_failures"] = core_failures
        if core_nulls:
            row["issues"].append("CORE_FIELD_NULL")
        if core_failures:
            row["issues"].append("CORE_VALUE_UNSUPPORTED")

        risk_name_newlines = [
            index for index, item in enumerate(product.get("investment_risks") or [])
            if "\n" in (item.get("name") or "")
        ]
        bad_risk_headings = [
            index for index, item in enumerate(product.get("investment_risks") or [])
            if re.sub(r"\s+", " ", item.get("name") or "").strip()
            in {"집합투자기구의 투자위험", "투자위험의 주요내용"}
        ]
        strategy = ((product.get("investment_strategy") or {}).get("text") or "")
        strategy_contamination = any(marker in strategy for marker in (
            "원금손실", "투자위험", "보장하지", "과거의 투자실적"
        ))
        row["risk_name_newlines"] = risk_name_newlines
        row["bad_risk_headings"] = bad_risk_headings
        row["strategy_contamination"] = strategy_contamination
        if risk_name_newlines:
            row["issues"].append("RISK_NAME_NEWLINE")
        if bad_risk_headings:
            row["issues"].append("RISK_SECTION_HEADING_AS_NAME")
        if strategy_contamination:
            row["issues"].append("STRATEGY_CONTAMINATION")

        numeric_failures = []
        for group in ("fees", "performance", "aum"):
            for index, item in enumerate(payload.get(group) or []):
                raw_cell = item.get("raw_cell_text")
                refs_for_item = item.get("evidence_refs") or []
                sources = []
                for ref in refs_for_item:
                    item_evidence = evidence.get(ref)
                    if not item_evidence:
                        continue
                    start = int(item_evidence.get("page_start") or 0)
                    end = int(item_evidence.get("page_end") or start)
                    sources.extend(direct_pages[max(0, start - 1):min(end, len(direct_pages))])
                if compact(raw_cell) and not numeric_supported(raw_cell, sources):
                    numeric_failures.append(f"{group}[{index}].raw_cell_text")
        row["numeric_evidence_failures"] = numeric_failures
        if numeric_failures:
            row["issues"].append("TABLE_VALUE_UNSUPPORTED")

        totals["documents"] += 1
        totals["hash_match"] += int(row["hash_match"])
        totals["page_count_match"] += int(row["page_count_match"])
        totals["corrupted_documents"] += int(row["replacement_characters"] > 0)
        totals["replacement_characters"] += row["replacement_characters"]
        totals["missing_evidence_refs"] += len(missing_refs)
        totals["evidence_total"] += len(evidence)
        totals["evidence_failures"] += len(evidence_failures)
        totals["core_nulls"] += len(core_nulls)
        totals["core_evidence_failures"] += len(core_failures)
        totals["numeric_evidence_failures"] += len(numeric_failures)
        totals["risk_name_newlines"] += len(risk_name_newlines)
        totals["bad_risk_headings"] += len(bad_risk_headings)
        totals["strategy_contamination"] += int(strategy_contamination)
        rows.append(row)

    duplicate_hashes = []
    for digest, members in hash_groups.items():
        if len(members) < 2:
            continue
        facts = [fact_view(
            {
                "product": payload.get("product"),
                "classes": payload.get("classes"),
                "fees": payload.get("fees"),
                "performance": payload.get("performance"),
                "aum": payload.get("aum"),
            }
        ) for _, payload in members
        ]
        duplicate_hashes.append({
            "document_hash": digest,
            "documents": [stem for stem, _ in members],
            "canonical_facts_equal": all(fact == facts[0] for fact in facts[1:]),
        })

    summary = dict(totals)
    summary["pdf_files"] = len(pdfs)
    summary["json_files"] = len(jsons)
    summary["documents_with_issues"] = sum(bool(row["issues"]) for row in rows)
    summary["duplicate_hash_groups"] = len(duplicate_hashes)
    summary["duplicate_hash_conflicts"] = sum(not item["canonical_facts_equal"] for item in duplicate_hashes)
    report = {"summary": summary, "duplicate_hashes": duplicate_hashes, "documents": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["hash_match"] != summary["documents"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
