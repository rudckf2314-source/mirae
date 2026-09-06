from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from schemas.product import CanonicalProduct
from utils.text import looks_mojibake

RISK_GRADE_MIN = 1
RISK_GRADE_MAX = 6

GRADE_LABELS = {
    1: "매우 높은 위험",
    2: "높은 위험",
    3: "다소 높은 위험",
    4: "보통 위험",
    5: "낮은 위험",
    6: "매우 낮은 위험",
}

BENCHMARK_NAMES = {"비교지수", "수익률 변동성", "수익률변동성"}
STRATEGY_RISK_MARKERS = ("추적오차", "베이시스위험", "원본손실", "유동성제약")


@dataclass
class CanonicalValidationResult:
    schema_warnings: list[str] = field(default_factory=list)
    consistency_warnings: list[str] = field(default_factory=list)


class CanonicalValidator:
    """Single validation boundary for an assembled CanonicalProduct.

    Pydantic owns structural constraints. This validator revalidates mutated
    models and reports domain/data-quality problems that should not make old
    cached documents unreadable.
    """

    def validate(self, product: CanonicalProduct) -> CanonicalValidationResult:
        result = CanonicalValidationResult()
        try:
            CanonicalProduct.model_validate(product.model_dump())
        except ValidationError as exc:
            result.schema_warnings.append(f"schema_validation_failed: {exc}")

        if product.document.document_type != "investment_prospectus":
            result.schema_warnings.append("document_type is not investment_prospectus")

        warnings = result.consistency_warnings
        self._validate_ranges(product, warnings)
        self._validate_text_quality(product, warnings)
        self._validate_evidence_sections(product, warnings)
        self._validate_domain_consistency(product, warnings)
        result.schema_warnings = list(dict.fromkeys(result.schema_warnings))
        result.consistency_warnings = list(dict.fromkeys(warnings))
        return result

    @staticmethod
    def _validate_ranges(product: CanonicalProduct, warnings: list[str]) -> None:
        grade = product.product.risk.grade
        if grade is not None and not (RISK_GRADE_MIN <= grade <= RISK_GRADE_MAX):
            warnings.append(
                f"risk.grade out of range: {grade} (expected {RISK_GRADE_MIN}-{RISK_GRADE_MAX})"
            )
        for fee in product.fees:
            if fee.rate is not None and fee.unit == "%" and not (-1.0 <= fee.rate <= 100.0):
                warnings.append(
                    f"fee.rate out of typical percent range: {fee.rate} "
                    f"({fee.class_name}/{fee.fee_type})"
                )
        for item in product.performance:
            if item.return_rate is not None and abs(item.return_rate) > 1000:
                warnings.append(
                    f"performance.return_rate looks implausible: {item.return_rate} "
                    f"({item.class_name}/{item.period})"
                )

    @staticmethod
    def _validate_text_quality(product: CanonicalProduct, warnings: list[str]) -> None:
        fields = {
            "product.name": product.product.name,
            "product.manager": product.product.manager,
            "product.asset_type": product.product.asset_type,
            "product.risk.label": product.product.risk.label,
            "product.investment_objective.text": product.product.investment_objective.text,
            "product.investment_strategy.text": product.product.investment_strategy.text,
        }
        for index, item in enumerate(product.classes):
            fields[f"classes[{index}].class_name"] = item.class_name
        for index, item in enumerate(product.evidence):
            fields[f"evidence[{index}].source_text"] = item.source_text
        for path, value in fields.items():
            if looks_mojibake(value):
                warnings.append(f"suspected mojibake at {path}")

    @staticmethod
    def _validate_evidence_sections(product: CanonicalProduct, warnings: list[str]) -> None:
        evidence_by_id = {item.chunk_id: item for item in product.evidence}
        for group_name, items, expected in (
            ("fees", product.fees, "FEES"),
            ("performance", product.performance, "PERFORMANCE"),
        ):
            for index, item in enumerate(items):
                for ref in item.evidence_refs:
                    evidence = evidence_by_id.get(ref)
                    section = evidence.section_type.value if evidence else None
                    if evidence is not None and section != expected:
                        warnings.append(
                            f"{group_name}[{index}] evidence section mismatch: "
                            f"{ref} is {section}, expected {expected}"
                        )

    @staticmethod
    def _validate_domain_consistency(product: CanonicalProduct, warnings: list[str]) -> None:
        grade = product.product.risk.grade
        label = product.product.risk.label
        if grade is not None and label and not looks_mojibake(label):
            expected = GRADE_LABELS.get(grade)
            compact_label = label.replace(" ", "")
            if expected and expected.replace(" ", "") not in compact_label:
                warnings.append(
                    f"risk grade/label mismatch: grade={grade}, label={label}, expected~={expected}"
                )

        class_names = {item.class_name for item in product.classes if item.class_name}
        for fee in product.fees:
            if fee.class_name and fee.class_name not in class_names:
                warnings.append(f"fee.class_name not in classes: {fee.class_name}")
        for row in product.performance:
            if row.class_name and row.class_name not in BENCHMARK_NAMES and row.class_name not in class_names:
                warnings.append(f"performance.class_name not in classes: {row.class_name}")

        strategy = product.product.investment_strategy.text or ""
        if not product.product.investment_risks and strategy and not looks_mojibake(strategy):
            if sum(marker in strategy for marker in STRATEGY_RISK_MARKERS) >= 2:
                warnings.append(
                    "investment_strategy contains risk descriptions but investment_risks is empty"
                )
