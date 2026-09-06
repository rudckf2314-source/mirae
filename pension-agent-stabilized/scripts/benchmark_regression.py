from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from benchmarking.regression import (
    BenchmarkReport,
    BenchmarkThresholds,
    evaluate_benchmark,
    run_regression_benchmark,
)
from config.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Repeat PDF extraction and measure reproducibility")
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".tmp" / "regression_benchmark")
    parser.add_argument("--thresholds", type=Path, help="JSON quality-gate thresholds")
    parser.add_argument("--evaluate-report", type=Path, help="Evaluate an existing benchmark JSON without API calls")
    args = parser.parse_args()
    thresholds = (
        BenchmarkThresholds.model_validate_json(args.thresholds.read_text(encoding="utf-8"))
        if args.thresholds else BenchmarkThresholds()
    )
    if args.evaluate_report:
        report = BenchmarkReport.model_validate_json(args.evaluate_report.read_text(encoding="utf-8"))
        report.gate = evaluate_benchmark(report, thresholds)
        print(json.dumps(report.gate.model_dump(), ensure_ascii=False, indent=2))
        return 0 if report.gate.passed else 2
    pdfs = args.pdfs or [
        ROOT / "data/cache/pdf/R2_KR5123490013.pdf",
        ROOT / "data/cache/pdf/R2_KR5123490017.pdf",
        ROOT / "data/cache/pdf/R2_KR510902511M.pdf",
    ]
    missing = [str(path) for path in pdfs if not path.exists()]
    if missing:
        parser.error(f"PDF not found: {', '.join(missing)}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or args.work_dir / f"benchmark_{stamp}.json"
    report = run_regression_benchmark(pdfs, get_settings(), args.work_dir / stamp)
    report.gate = evaluate_benchmark(report, thresholds)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    print(f"report={output}")
    return 0 if report.gate.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
