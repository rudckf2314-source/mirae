"""Validate immutable source JSON and optionally write derived normalization artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .product_db_adapter import JsonProductDBAdapter


def source_hashes(path: Path) -> dict[str, str]:
    files = [path] if path.is_file() else sorted(path.glob("*.json"))
    return {file.name: sha256(file.read_bytes()).hexdigest() for file in files}


def validate_products(path: str | Path) -> tuple[JsonProductDBAdapter, dict[str, Any]]:
    source_path = Path(path)
    before = source_hashes(source_path)
    adapter = JsonProductDBAdapter(source_path)
    after = source_hashes(source_path)
    normalizations = list(adapter.validation_report["normalizations"])
    missing = _missing_field_counts(source_path)

    methods = Counter(item["normalization_method"] for item in normalizations)
    unknown = [item for item in normalizations if item["requires_review"]]
    report: dict[str, Any] = {
        "checked_file_count": adapter.file_count,
        "json_syntax_error_count": adapter.validation_report["json_syntax_errors"],
        "schema_error_count": adapter.validation_report["schema_errors"],
        "schema_versions": _schema_versions(source_path),
        "missing_field_counts": dict(missing),
        "raw_record_count": adapter.raw_record_count,
        "deduplicated_record_count": adapter.record_count,
        "python_rule_count": methods.get("python_rule", 0),
        "llm_count": methods.get("llm", 0),
        "llm_failure_count": adapter.normalizer.llm_failures,
        "unknown_count": sum("UNKNOWN" in item["pension_type_codes"] for item in normalizations),
        "requires_review_count": len(unknown),
        "normalizer": adapter.normalizer.report(),
        "source_hashes_unchanged": before == after,
    }
    return adapter, report


def write_artifacts(path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    adapter, report = validate_products(path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    normalizations = adapter.validation_report["normalizations"]
    unresolved = [item for item in normalizations if item["requires_review"]]
    artifacts = {
        # Match the Product DB search surface: the Adapter has already applied
        # its established source-version deduplication to these 1,072 records.
        "product_normalized_index.json": adapter.records,
        "product_normalization_mapping.json": normalizations,
        "product_validation_report.json": report,
        "product_unresolved_report.json": unresolved,
    }
    for name, data in artifacts.items():
        (output / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return report


def _schema_versions(path: Path) -> dict[str, int]:
    result: Counter[str] = Counter()
    files = [path] if path.is_file() else sorted(path.glob("*.json"))
    for file in files:
        try:
            value = json.loads(file.read_text(encoding="utf-8")).get("schema_version")
            result[str(value)] += 1
        except (OSError, json.JSONDecodeError):
            continue
    return dict(result)


def _missing_field_counts(path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    files = [path] if path.is_file() else sorted(path.glob("*.json"))
    for file in files:
        try:
            document = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for product_class in document.get("classes") or []:
            if isinstance(product_class, dict) and product_class.get("is_online") is None:
                counts["classes[].is_online"] += 1
        for fee in document.get("fees") or []:
            if isinstance(fee, dict) and fee.get("rate") is None:
                counts["fees[].rate"] += 1
        if (document.get("source_document") or {}).get("file_hash") is None:
            counts["source_document.file_hash"] += 1
        if (document.get("product") or {}).get("inception_date") is None:
            counts["product.inception_date"] += 1
        if (document.get("product") or {}).get("is_high_complexity_product") is None:
            counts["product.is_high_complexity_product"] += 1
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/standard_json")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = write_artifacts(args.input, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
