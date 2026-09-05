from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from config.settings import Settings
from exceptions import DeterminismConflictError
from schemas.product import CanonicalProduct, ExtractionRunReport
from services.extraction_service import ExtractionService
from utils.determinism import canonical_fact_fingerprint


class RunComparison(BaseModel):
    fingerprint_match: bool
    metadata_match: bool
    classes_match: bool
    table_facts_match: bool
    objective_match: bool
    strategy_match: bool
    risks_match: bool


class BenchmarkDocumentReport(BaseModel):
    file_name: str
    document_hash: str | None = None
    cold_a: ExtractionRunReport | None = None
    cold_b: ExtractionRunReport | None = None
    checkpoint_replay: ExtractionRunReport | None = None
    comparison: RunComparison | None = None
    checkpoint_speedup: float | None = None
    llm_call_reduction: float | None = None
    determinism_conflict: bool = False
    errors: list[str] = Field(default_factory=list)


class BenchmarkReport(BaseModel):
    documents: list[BenchmarkDocumentReport]
    reproducibility_rate: float
    metadata_match_rate: float
    classes_match_rate: float
    table_facts_match_rate: float
    objective_match_rate: float
    strategy_match_rate: float
    risks_match_rate: float
    fingerprint_conflicts: int
    gate: "BenchmarkGateResult | None" = None


class BenchmarkThresholds(BaseModel):
    min_reproducibility_rate: float = 1.0
    min_metadata_match_rate: float = 1.0
    min_classes_match_rate: float = 1.0
    min_table_facts_match_rate: float = 1.0
    min_objective_match_rate: float = 1.0
    min_strategy_match_rate: float = 1.0
    min_risks_match_rate: float = 1.0
    max_fingerprint_conflicts: int = 0
    max_document_errors: int = 0
    min_checkpoint_cache_hits: int = 3


class BenchmarkGateResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)


def compare_runs(left: CanonicalProduct, right: CanonicalProduct) -> RunComparison:
    def compact(value: str | None) -> str:
        return re.sub(r"\s+", "", value or "")

    def facts(items: list) -> list[dict]:
        rows = []
        for item in items:
            row = item.model_dump(exclude={"evidence_refs"})
            rows.append(_normalize(row))
        return sorted(rows, key=str)

    left_meta = _normalize({
        "name": left.product.name,
        "manager": left.product.manager,
        "asset_type": left.product.asset_type,
        "fund_code": left.product.fund_code,
        "classification": left.product.classification,
        "risk_grade": left.product.risk.grade,
        "risk_label": left.product.risk.label,
        "as_of_date": left.document.as_of_date,
        "effective_date": left.document.effective_date,
    })
    right_meta = _normalize({
        "name": right.product.name,
        "manager": right.product.manager,
        "asset_type": right.product.asset_type,
        "fund_code": right.product.fund_code,
        "classification": right.product.classification,
        "risk_grade": right.product.risk.grade,
        "risk_label": right.product.risk.label,
        "as_of_date": right.document.as_of_date,
        "effective_date": right.document.effective_date,
    })
    return RunComparison(
        fingerprint_match=canonical_fact_fingerprint(left) == canonical_fact_fingerprint(right),
        metadata_match=left_meta == right_meta,
        classes_match=facts(left.classes) == facts(right.classes),
        table_facts_match=(
            facts(left.fees) == facts(right.fees)
            and facts(left.performance) == facts(right.performance)
            and facts(left.aum) == facts(right.aum)
        ),
        objective_match=compact(left.product.investment_objective.text) == compact(right.product.investment_objective.text),
        strategy_match=compact(left.product.investment_strategy.text) == compact(right.product.investment_strategy.text),
        risks_match=facts(left.product.investment_risks) == facts(right.product.investment_risks),
    )


def run_regression_benchmark(
    pdfs: list[Path],
    settings: Settings,
    work_dir: Path,
    service_factory: Callable[[Settings], ExtractionService] = ExtractionService,
) -> BenchmarkReport:
    reports: list[BenchmarkDocumentReport] = []
    for pdf in pdfs:
        report = BenchmarkDocumentReport(file_name=pdf.name)
        try:
            first_service = service_factory(_isolated_settings(settings, work_dir / pdf.stem / "run_a"))
            second_service = service_factory(_isolated_settings(settings, work_dir / pdf.stem / "run_b"))
            first = first_service.process_pdf(pdf, force=True)
            second = second_service.process_pdf(pdf, force=True)
            if not first.product or not second.product:
                raise RuntimeError("cold run returned no canonical product")
            report.document_hash = first.product.document.document_hash
            report.cold_a = first.product.extraction.run_report
            report.cold_b = second.product.extraction.run_report
            report.comparison = compare_runs(first.product, second.product)
            try:
                replay = first_service.process_pdf(pdf, force=True)
                if replay.product:
                    report.checkpoint_replay = replay.product.extraction.run_report
                    report.checkpoint_speedup = _reduction(
                        report.cold_a.total_duration_ms,
                        report.checkpoint_replay.total_duration_ms,
                    )
                    report.llm_call_reduction = _reduction(
                        float(report.cold_a.llm_calls),
                        float(report.checkpoint_replay.llm_calls),
                    )
            except DeterminismConflictError as exc:
                report.determinism_conflict = True
                report.errors.append(str(exc))
        except Exception as exc:
            report.errors.append(str(exc))
        reports.append(report)

    comparable = [item for item in reports if item.comparison is not None]
    report = BenchmarkReport(
        documents=reports,
        reproducibility_rate=_rate(comparable, lambda item: item.comparison.fingerprint_match),
        metadata_match_rate=_rate(comparable, lambda item: item.comparison.metadata_match),
        classes_match_rate=_rate(comparable, lambda item: item.comparison.classes_match),
        table_facts_match_rate=_rate(comparable, lambda item: item.comparison.table_facts_match),
        objective_match_rate=_rate(comparable, lambda item: item.comparison.objective_match),
        strategy_match_rate=_rate(comparable, lambda item: item.comparison.strategy_match),
        risks_match_rate=_rate(comparable, lambda item: item.comparison.risks_match),
        fingerprint_conflicts=sum(item.determinism_conflict for item in reports),
    )
    report.gate = evaluate_benchmark(report, BenchmarkThresholds())
    return report


def evaluate_benchmark(
    report: BenchmarkReport,
    thresholds: BenchmarkThresholds,
) -> BenchmarkGateResult:
    violations: list[str] = []
    rate_checks = (
        ("reproducibility_rate", report.reproducibility_rate, thresholds.min_reproducibility_rate),
        ("metadata_match_rate", report.metadata_match_rate, thresholds.min_metadata_match_rate),
        ("classes_match_rate", report.classes_match_rate, thresholds.min_classes_match_rate),
        ("table_facts_match_rate", report.table_facts_match_rate, thresholds.min_table_facts_match_rate),
        ("objective_match_rate", report.objective_match_rate, thresholds.min_objective_match_rate),
        ("strategy_match_rate", report.strategy_match_rate, thresholds.min_strategy_match_rate),
        ("risks_match_rate", report.risks_match_rate, thresholds.min_risks_match_rate),
    )
    for name, actual, minimum in rate_checks:
        if actual < minimum:
            violations.append(f"{name}={actual:.4f} < minimum={minimum:.4f}")
    if report.fingerprint_conflicts > thresholds.max_fingerprint_conflicts:
        violations.append(
            f"fingerprint_conflicts={report.fingerprint_conflicts} > maximum={thresholds.max_fingerprint_conflicts}"
        )
    error_count = sum(bool(item.errors) for item in report.documents)
    if error_count > thresholds.max_document_errors:
        violations.append(f"document_errors={error_count} > maximum={thresholds.max_document_errors}")
    for item in report.documents:
        replay = item.checkpoint_replay
        if replay and replay.cache_hits < thresholds.min_checkpoint_cache_hits:
            violations.append(
                f"{item.file_name}: checkpoint_cache_hits={replay.cache_hits} "
                f"< minimum={thresholds.min_checkpoint_cache_hits}"
            )
    return BenchmarkGateResult(passed=not violations, violations=violations)


def _isolated_settings(settings: Settings, root: Path) -> Settings:
    return settings.model_copy(update={
        "cache_dir": root / "cache",
        "standard_json_dir": root / "standard_json",
        "db_auto_save": False,
    })


def _normalize(value):
    if isinstance(value, str):
        return re.sub(r"\s+", "", value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _rate(items: list, predicate) -> float:
    return round(sum(1 for item in items if predicate(item)) / len(items), 4) if items else 0.0


def _reduction(cold: float, replay: float) -> float | None:
    if cold <= 0:
        return None
    return round((cold - replay) / cold, 4)
