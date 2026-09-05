import re

from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable
from schemas.product import CanonicalProduct
from parsers.table_parser import is_semantic_risk_table

from processing.class_candidates import (
    class_identity,
    harvest_class_candidates,
    normalize_class_name,
)

NAMED_RISK_MARKERS = (
    "추적오차",
    "베이시스위험",
    "원본손실위험",
    "주요 투자위험",
    "유동성 제약",
    "지수 관련 위험",
)
GRADE_ONLY_MARKERS = (
    "매우 높은 위험",
    "높은 위험",
    "다소 높은 위험",
    "보통 위험",
    "낮은 위험",
    "매우 낮은 위험",
)


class CompletenessValidator:
    def validate(
        self,
        product: CanonicalProduct,
        chunks: list[Chunk],
        tables: list[DetectedTable] | None = None,
    ) -> tuple[list[str], list[str]]:
        warnings: list[str] = []
        missing: list[str] = []
        detected = {chunk.section_type for chunk in chunks}
        ownership = {
            (item.owner, item.field): item.status
            for item in product.extraction.ownership
        }
        for table in tables or []:
            detected.add(table.section_type)

        if not product.product.name:
            missing.append("product.name")
            warnings.append("필수 필드 product.name이 없습니다.")
        if not product.product.manager:
            missing.append("product.manager")
            warnings.append("필수 필드 product.manager가 없습니다.")
        if product.product.risk.grade is None:
            missing.append("product.risk.grade")
            warnings.append("필수 필드 product.risk.grade가 없습니다.")
        if not product.document.file_name:
            missing.append("document.file_name")
        cover_text = "\n".join(
            chunk.text or "" for chunk in chunks if chunk.page_start <= 3 and not chunk.table_id
        )
        if ("작성 기준일" in cover_text or "작성기준일" in cover_text) and not product.document.as_of_date:
            warnings.append("metadata_missing_date: as_of_date")
        if "효력발생일" in cover_text and not product.document.effective_date:
            warnings.append("metadata_missing_date: effective_date")

        if SectionType.INVESTMENT_OBJECTIVE in detected and not (product.product.investment_objective.text or "").strip():
            missing.append("investment_objective")
            warnings.append("투자목적 섹션이 탐지되었으나 추출 결과가 없습니다.")
        if SectionType.INVESTMENT_STRATEGY in detected and not (product.product.investment_strategy.text or "").strip():
            missing.append("investment_strategy")
            warnings.append("투자전략 섹션이 탐지되었으나 추출 결과가 없습니다.")
        if SectionType.PERFORMANCE in detected and not product.performance:
            from validators.source_absent import is_performance_source_absent

            if is_performance_source_absent(chunks, tables):
                warnings.append(
                    "INFO: SOURCE_ABSENT: performance (신규설정/해당사항 없음)"
                )
            else:
                missing.append("performance")
                warnings.append("수익률 표가 탐지되었으나 데이터 추출 결과가 없습니다.")
        if SectionType.FEES in detected and not product.fees:
            missing.append("fees")
            warnings.append("보수/수수료 표가 탐지되었으나 데이터 추출 결과가 없습니다.")
        for field in ("fees", "performance"):
            table_status = ownership.get(("table", field))
            if table_status in {"AMBIGUOUS", "REJECTED"}:
                if field == "performance":
                    from validators.source_absent import is_performance_source_absent

                    if is_performance_source_absent(chunks, tables):
                        warnings.append(
                            f"INFO: SOURCE_ABSENT: table_gate_{table_status.lower()}: performance"
                        )
                        continue
                warnings.append(f"table_gate_{table_status.lower()}: {field}")
        if self._aum_detected(chunks, detected) and not product.aum:
            missing.append("aum")
            warnings.append("운용규모 섹션이 탐지되었으나 데이터 추출 결과가 없습니다.")

        if self._investment_risk_expected(chunks, detected, tables or []) and not product.product.investment_risks:
            missing.append("investment_risks")
            warnings.append("Investment risk section was detected but no structured investment_risks were extracted.")

        source_classes = set(harvest_class_candidates(chunks, tables))
        final_classes = {normalize_class_name(item.class_name) for item in product.classes if item.class_name}
        final_idents = {class_identity(name) for name in final_classes}
        if (SectionType.CLASS_INFO in detected or SectionType.FEES in detected) and not product.classes:
            missing.append("classes")
            warnings.append("클래스 정보가 탐지되었으나 추출 결과가 없습니다.")
        missing_classes = sorted(
            name
            for name in source_classes
            if name not in final_classes and class_identity(name) not in final_idents
        )
        for name in missing_classes:
            warnings.append(f"Class {name}가 Source에서는 탐지되었지만 final classes에 존재하지 않습니다.")

        return list(dict.fromkeys(warnings)), list(dict.fromkeys(missing))

    def _aum_detected(self, chunks: list[Chunk], detected: set[SectionType]) -> bool:
        fund_aum_heading = re.compile(r"(?:운용규모|순자산총액)\s*[:：]\s*[\d,]+")
        for chunk in chunks:
            text = chunk.text or ""
            if "운용전문" in text[:160] or "동종집합투자기구" in text[:240]:
                continue
            prefix = text[:200]
            if not fund_aum_heading.search(prefix):
                continue
            if "해당사항 없음" in prefix or "해당없음" in prefix.replace(" ", ""):
                continue
            return True
        return False

    def _investment_risk_expected(
        self,
        chunks: list[Chunk],
        detected: set[SectionType],
        tables: list[DetectedTable],
    ) -> bool:
        if any(is_semantic_risk_table(table) for table in tables):
            return True
        if SectionType.INVESTMENT_RISK in detected:
            return True
        for chunk in chunks:
            text = chunk.text or ""
            if chunk.section_type == SectionType.RISK_GRADE and not any(marker in text for marker in NAMED_RISK_MARKERS):
                continue
            hits = sum(1 for marker in NAMED_RISK_MARKERS if marker in text)
            if hits >= 2:
                return True
        return False
