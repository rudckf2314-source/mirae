"""Align persisted canonical JSON with current-code extraction (no risk-rule changes).

Same-run checks:
1) snapshot pre-persist investment_risks
2) save
3) re-read disk JSON
4) compare count / names / descriptions / evidence_refs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import Settings, get_settings  # noqa: E402
from processing.risk_heading_anchor import is_generic_risk_heading  # noqa: E402
from processing.risk_row_extractor import compact_risk_text  # noqa: E402
from schemas.product import CanonicalProduct  # noqa: E402
from services.extraction_service import ExtractionService  # noqa: E402


def compact_name(text: str | None) -> str:
    return compact_risk_text(text)


def risk_payload(product: CanonicalProduct) -> list[dict]:
    rows = []
    for item in product.product.investment_risks:
        rows.append(
            {
                "name": item.name or "",
                "normalized_name": compact_name(item.name),
                "description": re.sub(r"\s+", " ", item.description or "").strip(),
                "evidence_refs": list(item.evidence_refs or []),
            }
        )
    rows.sort(key=lambda row: (row["normalized_name"], row["description"]))
    return rows


def names_hash(rows: list[dict]) -> str:
    blob = "\n".join(row["normalized_name"] for row in rows)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compare_risks(pre: list[dict], disk: list[dict]) -> list[str]:
    mismatches: list[str] = []
    if len(pre) != len(disk):
        mismatches.append(f"count:{len(pre)}!={len(disk)}")
    pre_names = [row["normalized_name"] for row in pre]
    disk_names = [row["normalized_name"] for row in disk]
    if pre_names != disk_names:
        mismatches.append(
            "names:"
            + json.dumps(
                {
                    "pre_only": sorted(set(pre_names) - set(disk_names)),
                    "disk_only": sorted(set(disk_names) - set(pre_names)),
                },
                ensure_ascii=False,
            )
        )
    pre_map = {row["normalized_name"]: row for row in pre}
    disk_map = {row["normalized_name"]: row for row in disk}
    for key in sorted(set(pre_map) & set(disk_map)):
        left = pre_map[key]
        right = disk_map[key]
        if left["description"] != right["description"]:
            mismatches.append(f"description:{key}")
        if left["evidence_refs"] != right["evidence_refs"]:
            mismatches.append(f"evidence_refs:{key}")
    return mismatches


def expand_with_hash_siblings(document_ids: list[str], settings: Settings) -> list[str]:
    """Duplicate-PDF hash groups must be cleared together to avoid fingerprint conflicts."""
    targets = set(document_ids)
    hash_of: dict[str, str] = {}
    for path in settings.extracted_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        document_id = payload.get("document", {}).get("document_id") or path.stem
        document_hash = payload.get("document", {}).get("document_hash")
        if document_id and document_hash:
            hash_of[document_id] = document_hash
    selected_hashes = {hash_of[doc] for doc in targets if doc in hash_of}
    if selected_hashes:
        for document_id, document_hash in hash_of.items():
            if document_hash in selected_hashes:
                targets.add(document_id)
    return sorted(targets)


def clear_stale_artifacts(document_ids: list[str], settings: Settings) -> dict[str, int]:
    removed = {"extracted": 0, "parsed": 0, "standard": 0, "index": 0}
    extracted_dir = settings.extracted_dir
    parsed_dir = settings.parsed_dir
    standard_dir = settings.standard_json_dir
    for document_id in document_ids:
        for path in extracted_dir.glob(f"{document_id}*.json"):
            path.unlink()
            removed["extracted"] += 1
        for path in parsed_dir.glob(f"{document_id}*.json"):
            path.unlink()
            removed["parsed"] += 1
        for path in standard_dir.glob(f"{document_id}*.json"):
            path.unlink()
            removed["standard"] += 1

    index_path = settings.index_path
    if index_path.exists():
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        docs = payload.get("documents") or []
        keep = [item for item in docs if item.get("document_id") not in set(document_ids)]
        removed["index"] = len(docs) - len(keep)
        payload["documents"] = keep
        index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def resolve_pdf(document_id: str) -> Path:
    for root in (ROOT / "cache", ROOT / "data" / "cache" / "pdf"):
        matches = sorted(root.glob(f"{document_id}*.pdf"))
        if matches:
            return matches[0]
    raise FileNotFoundError(document_id)


def process_one(service: ExtractionService, document_id: str) -> dict:
    pdf_path = resolve_pdf(document_id)
    pre_snapshot: dict | None = None
    original_save = service.repository.save_product

    def capturing_save(product, pdf_bytes=None, parsed=None):
        nonlocal pre_snapshot
        rows = risk_payload(product)
        pre_snapshot = {
            "risk_count": len(rows),
            "normalized_names_hash": names_hash(rows),
            "evidence_ref_count": sum(len(row["evidence_refs"]) for row in rows),
            "rows": rows,
            "document_id": product.document.document_id,
        }
        return original_save(product, pdf_bytes=pdf_bytes, parsed=parsed)

    service.repository.save_product = capturing_save  # type: ignore[method-assign]
    try:
        result = service.process_pdf(pdf_path, file_name=pdf_path.name, force=True)
    finally:
        service.repository.save_product = original_save  # type: ignore[method-assign]

    if result.error or result.product is None or pre_snapshot is None:
        return {
            "document_id": document_id,
            "ok": False,
            "error": result.error or "missing product/pre-persist snapshot",
        }

    document_id = result.product.document.document_id
    disk_path = service.settings.extracted_dir / f"{document_id}.json"
    disk_product = CanonicalProduct.model_validate_json(disk_path.read_text(encoding="utf-8"))
    disk_rows = risk_payload(disk_product)
    mismatches = compare_risks(pre_snapshot["rows"], disk_rows)
    return {
        "document_id": document_id,
        "ok": not mismatches,
        "pre_persist_risk_count": pre_snapshot["risk_count"],
        "disk_risk_count": len(disk_rows),
        "normalized_names_hash": pre_snapshot["normalized_names_hash"],
        "disk_names_hash": names_hash(disk_rows),
        "evidence_ref_count": pre_snapshot["evidence_ref_count"],
        "disk_evidence_ref_count": sum(len(row["evidence_refs"]) for row in disk_rows),
        "output_file_path": str(disk_path),
        "standard_json_path": result.standard_json_path,
        "db_saved": result.db_saved,
        "db_error": result.db_error,
        "status": result.product.extraction.status,
        "verify": result.product.extraction.verification.status,
        "mismatches": mismatches,
        "pre_persist_names": [row["name"] for row in pre_snapshot["rows"]],
        "disk_names": [row["name"] for row in disk_rows],
    }


def audit_quality(document_ids: list[str], settings: Settings) -> dict:
    generic = []
    unsupported = []
    missing_evidence = []
    schema_fail = []
    evidence_fail = []
    counts = {}
    total = 0
    for document_id in document_ids:
        path = settings.extracted_dir / f"{document_id}.json"
        if not path.exists():
            schema_fail.append(document_id)
            continue
        product = CanonicalProduct.model_validate_json(path.read_text(encoding="utf-8"))
        risks = product.product.investment_risks
        counts[document_id] = len(risks)
        total += len(risks)
        chunk_texts = {
            item.chunk_id: item.source_text
            for item in product.evidence
        }
        for risk in risks:
            if is_generic_risk_heading(risk.name):
                generic.append({"document_id": document_id, "name": risk.name})
            if not risk.evidence_refs:
                missing_evidence.append({"document_id": document_id, "name": risk.name})
                continue
            evidence = "".join(chunk_texts.get(ref, "") for ref in risk.evidence_refs)
            if compact_name(risk.name) not in compact_name(evidence):
                # evidence list may omit unused chunks; fall back to joined refs presence
                unsupported.append({"document_id": document_id, "name": risk.name})
        validation = product.extraction.validation
        if validation.schema_status != "PASS":
            schema_fail.append(document_id)
        if validation.evidence_status != "PASS":
            evidence_fail.append(document_id)
    missing_files = [
        document_id
        for document_id in document_ids
        if not (settings.extracted_dir / f"{document_id}.json").exists()
    ]
    return {
        "documents": len(document_ids),
        "json_files": len(document_ids) - len(missing_files),
        "missing_files": missing_files,
        "total_risks": total,
        "risk_counts": counts,
        "generic_heading_fp": generic,
        "unsupported_risk_name": unsupported,
        "missing_evidence_ref": missing_evidence,
        "schema_fail": sorted(set(schema_fail)),
        "evidence_fail": sorted(set(evidence_fail)),
        "schema_pass": len(document_ids) - len(set(schema_fail) | set(missing_files)),
        "evidence_pass": len(document_ids) - len(set(evidence_fail) | set(missing_files)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("22", "100", "both"), default="both")
    parser.add_argument("--document-id", action="append", default=[])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    # Prefer JSON alignment first; DB save may still run if configured.
    settings = Settings(**{**settings.model_dump(), "llm_fail_fast": False})

    audit = json.loads(
        (ROOT / "data" / "cache" / "risk_stage_divergence_audit.json").read_text(encoding="utf-8")
    )
    delta = next(item for item in audit["stage_deltas"] if item["from"] == "8_pre_persist_object")
    skewed = sorted({item["document_id"] for item in delta["increased"] + delta["decreased"]})
    all_docs = sorted(
        {
            path.stem
            for root in (ROOT / "cache", ROOT / "data" / "cache" / "pdf")
            for path in root.glob("*.pdf")
        }
    )

    service = ExtractionService(settings=settings)
    report: dict = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "phase_22": None,
        "phase_100": None,
        "memory_vs_replay_11": [],
    }

    # Separate note: 784 vs 787 (do not mix into persistence alignment).
    memory = json.loads(
        (ROOT / "data" / "cache" / "risk_precision_all_after.json").read_text(encoding="utf-8")
    )
    memory_counts = {item["document_id"]: item for item in memory["results"]}
    for document_id, meta in audit["document_meta"].items():
        if meta["memory_report_count"] == meta["chain_count"]:
            continue
        mem_item = memory_counts.get(document_id) or {}
        report["memory_vs_replay_11"].append(
            {
                "document_id": document_id,
                "memory_report_count": meta["memory_report_count"],
                "current_replay_count": meta["chain_count"],
                "old_disk_count_at_audit": meta["disk_count"],
                "delta_replay_minus_memory": meta["chain_count"] - meta["memory_report_count"],
                "memory_names": mem_item.get("risk_names") or [],
                "replay_names": meta.get("chain_names") or [],
                "likely_cause": (
                    "in-memory audit used verify_risk_regression path / earlier code snapshot; "
                    "current chain replay uses latest apply_narrative_facts + page-window filters"
                ),
            }
        )

    def run_phase(name: str, document_ids: list[str]) -> dict:
        clear_ids = expand_with_hash_siblings(document_ids, settings)
        logging.info(
            "=== phase %s clear stale artifacts: targets=%s cleared_set=%s ===",
            name,
            len(document_ids),
            len(clear_ids),
        )
        cleared = clear_stale_artifacts(clear_ids, settings)
        logging.info("cleared=%s", cleared)
        rows = []
        for index, document_id in enumerate(document_ids, start=1):
            logging.info("=== [%s/%s] %s ===", index, len(document_ids), document_id)
            try:
                row = process_one(service, document_id)
            except Exception as exc:  # noqa: BLE001
                logging.exception("failed %s", document_id)
                row = {"document_id": document_id, "ok": False, "error": str(exc)}
            rows.append(row)
            logging.info(
                "%s ok=%s pre=%s disk=%s mismatches=%s",
                document_id,
                row.get("ok"),
                row.get("pre_persist_risk_count"),
                row.get("disk_risk_count"),
                row.get("mismatches"),
            )
        matched = sum(1 for row in rows if row.get("ok"))
        quality = audit_quality([row["document_id"] for row in rows if row.get("ok")], settings)
        # same-run aggregate
        same_run_mismatch = [row for row in rows if not row.get("ok")]
        total_disk = sum(int(row.get("disk_risk_count") or 0) for row in rows if row.get("ok"))
        total_pre = sum(int(row.get("pre_persist_risk_count") or 0) for row in rows if row.get("ok"))
        return {
            "documents": len(document_ids),
            "matched": matched,
            "mismatched": len(same_run_mismatch),
            "same_run_mismatch_docs": same_run_mismatch,
            "pre_persist_total": total_pre,
            "disk_total": total_disk,
            "cleared": cleared,
            "results": rows,
            "quality": quality,
        }

    targets_22 = args.document_id or skewed
    if args.phase in {"22", "both"}:
        phase22 = run_phase("22", targets_22)
        report["phase_22"] = phase22
        out22 = ROOT / "data" / "cache" / "risk_persist_align_22.json"
        out22.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if phase22["mismatched"]:
            logging.error("22/22 failed; stop before full 100. mismatches=%s", phase22["mismatched"])
            report["stopped_before_100"] = True
            (ROOT / "data" / "cache" / "risk_persist_align_final.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return 2
        logging.info("22/22 pre-persist == disk confirmed")

    if args.phase in {"100", "both"}:
        if args.phase == "both" and report.get("phase_22") and report["phase_22"]["mismatched"]:
            return 2
        phase100 = run_phase("100", all_docs)
        report["phase_100"] = phase100
        report["final_persisted_risk_total"] = phase100["disk_total"]
        report["artifact_skew_resolved"] = (
            phase100["mismatched"] == 0
            and phase100["quality"]["json_files"] == 100
            and phase100["quality"]["generic_heading_fp"] == []
            and phase100["quality"]["unsupported_risk_name"] == []
            and phase100["quality"]["missing_evidence_ref"] == []
            and phase100["quality"]["schema_pass"] == 100
            and phase100["quality"]["evidence_pass"] == 100
        )

    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = ROOT / "data" / "cache" / "risk_persist_align_final.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("wrote %s", out)
    logging.info(
        "summary matched_100=%s disk_total=%s skew_resolved=%s",
        None if not report.get("phase_100") else report["phase_100"]["matched"],
        report.get("final_persisted_risk_total"),
        report.get("artifact_skew_resolved"),
    )
    if report.get("phase_100") and report["phase_100"]["mismatched"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
