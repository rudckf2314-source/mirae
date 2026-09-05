from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from schemas.chunk import Chunk
from schemas.document import DetectedTable
from schemas.product import CanonicalProduct, VerificationItem, VerificationReport
from verification.deterministic import evidence_texts, verify_text
from verification.text import contains_text
from verification.narrative import verify_narrative
from verification.table import verify_fee, verify_performance


class VerificationPipeline:
    """Judge whether extracted values are supported by source evidence. Does not mutate those values."""

    def verify(
        self,
        product: CanonicalProduct,
        chunks: list[Chunk],
        tables: list[DetectedTable] | None = None,
        llm: BaseChatModel | None = None,
    ) -> CanonicalProduct:
        items: list[VerificationItem] = []
        tables = tables or []
        info = product.product
        cover_refs = list(dict.fromkeys(info.risk.evidence_refs + _cover_refs(chunks)))
        name_refs = list(dict.fromkeys(cover_refs + _ownership_refs(product, "name")))
        manager_refs = list(dict.fromkeys(cover_refs + _ownership_refs(product, "manager")))

        items.append(
            verify_text(
                "product.name",
                info.name,
                evidence_texts(product, name_refs, chunks),
                name_refs,
            )
        )
        items.append(
            verify_text(
                "product.manager",
                info.manager,
                evidence_texts(product, manager_refs, chunks),
                manager_refs,
            )
        )

        # 펀드코드는 표지보다 요약정보/제1부 명칭 표에만 나타나는 문서가 있다.
        # exact code가 실제 비표 chunk에 존재하면 그 chunk를 검증 근거에 추가한다.
        fund_refs = list(cover_refs)
        if info.fund_code:
            fund_refs.extend(
                chunk.chunk_id
                for chunk in chunks
                if not chunk.table_id
                and chunk.page_start <= 15
                and contains_text(chunk.text, info.fund_code)
            )
        fund_refs = list(dict.fromkeys(fund_refs))
        items.append(
            verify_text(
                "product.fund_code",
                info.fund_code,
                evidence_texts(product, fund_refs, chunks),
                fund_refs,
            )
        )
        items.append(
            verify_text(
                "product.risk.grade",
                None if info.risk.grade is None else str(info.risk.grade),
                evidence_texts(product, info.risk.evidence_refs or cover_refs, chunks),
                info.risk.evidence_refs or cover_refs,
                kind="grade",
            )
        )
        items.append(
            verify_text(
                "product.risk.label",
                info.risk.label,
                evidence_texts(product, info.risk.evidence_refs or cover_refs, chunks),
                info.risk.evidence_refs or cover_refs,
            )
        )
        as_of_refs = list(dict.fromkeys(cover_refs + _ownership_refs(product, "as_of_date")))
        effective_refs = list(
            dict.fromkeys(cover_refs + _ownership_refs(product, "effective_date"))
        )
        items.append(
            verify_text(
                "document.as_of_date",
                product.document.as_of_date,
                evidence_texts(product, as_of_refs, chunks),
                as_of_refs,
                kind="date",
            )
        )
        items.append(
            verify_text(
                "document.effective_date",
                product.document.effective_date,
                evidence_texts(product, effective_refs, chunks),
                effective_refs,
                kind="date",
            )
        )
        items.append(
            verify_narrative(
                "product.investment_objective",
                info.investment_objective.text,
                evidence_texts(product, info.investment_objective.evidence_refs, chunks),
                info.investment_objective.evidence_refs,
                llm=llm,
                chunks=chunks,
                product=product,
                role="objective",
                sibling_text=info.investment_strategy.text,
                sibling_refs=info.investment_strategy.evidence_refs,
            )
        )
        items.append(
            verify_narrative(
                "product.investment_strategy",
                info.investment_strategy.text,
                evidence_texts(product, info.investment_strategy.evidence_refs, chunks),
                info.investment_strategy.evidence_refs,
                llm=llm,
                chunks=chunks,
                product=product,
                role="strategy",
                sibling_text=info.investment_objective.text,
                sibling_refs=info.investment_objective.evidence_refs,
            )
        )
        for index, risk in enumerate(info.investment_risks):
            if not (risk.description or "").strip():
                items.append(
                    VerificationItem(
                        field_path=f"product.investment_risks[{index}].description",
                        status="WARNING",
                        verdict="MISSING_DESCRIPTION",
                        method="deterministic",
                        extracted_value=risk.name,
                        evidence_refs=list(risk.evidence_refs),
                        reason="위험명은 추출되었지만 근거로 확인 가능한 완결 설명이 없습니다.",
                    )
                )
                continue
            items.append(
                verify_narrative(
                    f"product.investment_risks[{index}].description",
                    risk.description,
                    evidence_texts(product, risk.evidence_refs, chunks),
                    risk.evidence_refs,
                    llm=llm,
                    role="risk",
                    risk_name=risk.name,
                )
            )
        for index, fee in enumerate(product.fees):
            items.append(verify_fee(f"fees[{index}]", fee, chunks, tables))
        for index, row in enumerate(product.performance):
            items.append(verify_performance(f"performance[{index}]", row, chunks, tables))

        product.extraction.verification = _summarize(items)
        return product


def _cover_refs(chunks: list[Chunk]) -> list[str]:
    return [chunk.chunk_id for chunk in chunks if chunk.page_start <= 2 and not chunk.table_id]


def _ownership_refs(product: CanonicalProduct, field: str) -> list[str]:
    refs: list[str] = []
    for item in product.extraction.ownership:
        if item.field == field:
            refs.extend(item.evidence_refs or [])
    return refs


def _summarize(items: list[VerificationItem]) -> VerificationReport:
    pass_count = sum(1 for item in items if item.status == "PASS")
    warning_count = sum(1 for item in items if item.status == "WARNING")
    fail_count = sum(1 for item in items if item.status == "FAIL")
    skipped_count = sum(1 for item in items if item.status == "SKIPPED")
    unverifiable = sum(
        1
        for item in items
        if item.status == "UNVERIFIABLE" or (item.status == "SKIPPED" and item.verdict == "UNVERIFIABLE")
    )
    if fail_count:
        status = "FAIL"
    elif warning_count or unverifiable:
        status = "WARNING"
    else:
        status = "PASS"
    return VerificationReport(
        status=status,
        checked=len(items),
        pass_count=pass_count,
        warning_count=warning_count,
        fail_count=fail_count,
        skipped_count=skipped_count,
        items=items,
    )
