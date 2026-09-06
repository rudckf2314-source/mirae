from __future__ import annotations

import re

from parsers.table_parser import reconstruct_fee_table, reconstruct_performance_table
from processing.class_candidates import (
    class_identity,
    is_plausible_class_name,
    normalize_class_name,
    prefer_class_name,
)
from processing.class_resolver import ClassResolver
from parsers.table_quality import assess_table, BROKEN
from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable
from schemas.extraction import LLMExtractionResult
from schemas.product import (
    CandidateOutcome,
    CanonicalProduct,
    FeeItem,
    OwnershipOutcome,
    PerformanceItem,
)

FEE_TYPES = (
    "sales_fee",
    "total_fee",
    "sales_remuneration",
    "peer_group_total_fee",
    "total_fee_and_expenses",
)
PERIODS = ("1Y", "2Y", "3Y", "5Y", "SINCE_INCEPTION")
DATE_NAME_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
STRUCTURED_METHODS = {
    "pymupdf_normalized",
    "text_fallback",
    "pdfplumber",
    "pdfminer_coordinate_fallback",
}
FUND_AGGREGATE_NAMES = ("투자신탁", "투자실적추이", "투자실적")


def extract_table_facts(
    tables: list[DetectedTable] | None,
    chunks: list[Chunk] | None = None,
) -> LLMExtractionResult:
    tables = list(tables or [])
    tables = _normalize_extractable_tables(tables)
    chunk_map = {chunk.table_id: chunk.chunk_id for chunk in chunks or [] if chunk.table_id}
    resolver = ClassResolver(chunks, tables)
    fee_candidates = [table for table in tables if _looks_like_fee_table(table)]
    fee_statuses = {
        _table_candidate_id(table): _fee_table_status(table, resolver)
        for table in fee_candidates
    }
    method_priority = {
        "text_fallback": 0,
        "pymupdf_normalized": 1,
        "pdfplumber": 2,
        "pdfminer_coordinate_fallback": 3,
        "pymupdf": 9,
    }
    valid_fee_tables = sorted(
        (
            table
            for table in fee_candidates
            if fee_statuses[_table_candidate_id(table)] == "VALID"
        ),
        key=lambda table: (table.page_number, method_priority.get(table.extraction_method, 8)),
    )
    _promote_fee_sections(valid_fee_tables, chunks)
    fee_pages = _first_valid_pages(valid_fee_tables)
    perf_pages = _summary_pages(tables, SectionType.PERFORMANCE)

    fees: list[FeeItem] = []
    seen_fees: set[tuple] = set()
    for table in valid_fee_tables:
        if fee_pages is not None and table.page_number not in fee_pages:
            continue
        refs = _refs(table, chunk_map)
        for row_index, row in enumerate(table.rows):
            for item in _fees_from_row(row, refs, resolver, table.headers, table.rows, row_index):
                key = (class_identity(item.class_name), item.fee_type)
                if key in seen_fees:
                    continue
                seen_fees.add(key)
                fees.append(item)
    if not fees:
        for item in _fees_from_summary_chunks(chunks or [], chunk_map):
            key = (class_identity(item.class_name), item.fee_type)
            if key in seen_fees:
                continue
            seen_fees.add(key)
            fees.append(item)
    else:
        # Summary 투자비용 blocks are the authoritative rate layout for single-share
        # funds; component-breakdown tables often shift 운용/판매 columns.
        summary = _fees_from_summary_chunks(chunks or [], chunk_map)
        if summary:
            fees = _merge_fees(summary, fees)

    performance: list[PerformanceItem] = []
    seen_perf: set[tuple] = set()
    class_rows_present = False
    pending_aggregate: list[PerformanceItem] = []
    for table in _iter_structured(tables, SectionType.PERFORMANCE, perf_pages):
        refs = _refs(table, chunk_map)
        for row_index, row in enumerate(table.rows):
            kind = _performance_kind(row[0] if row else "")
            if kind == "unlabeled":
                continue
            items = _performance_from_row(row, refs, kind, row_index)
            if kind == "class":
                class_rows_present = True
                for item in items:
                    key = _perf_key(item)
                    if key in seen_perf:
                        continue
                    seen_perf.add(key)
                    performance.append(item)
            elif kind in {"benchmark", "volatility"}:
                for item in items:
                    key = _perf_key(item)
                    if key in seen_perf:
                        continue
                    seen_perf.add(key)
                    performance.append(item)
            else:
                pending_aggregate.extend(items)
    if not class_rows_present:
        for item in pending_aggregate:
            key = _perf_key(item)
            if key in seen_perf:
                continue
            seen_perf.add(key)
            performance.append(item)

    ownership = [
        OwnershipOutcome(
            field="fees",
            owner="table",
            status=_aggregate_table_status(fee_candidates, valid_fee_tables, fees),
            reason=_table_status_reason("fee", fee_candidates, valid_fee_tables, fees),
            evidence_refs=list(dict.fromkeys(ref for item in fees for ref in item.evidence_refs)),
        ),
        OwnershipOutcome(
            field="performance",
            owner="table",
            status=_performance_status(tables, performance),
            reason=None if performance else "No validated performance rows were extracted.",
            evidence_refs=list(
                dict.fromkeys(ref for item in performance for ref in item.evidence_refs)
            ),
        ),
    ]
    candidate_outcomes = [
        CandidateOutcome(
            field="fees",
            owner="table",
            candidate_id=_table_candidate_id(table),
            status=fee_statuses[_table_candidate_id(table)],
            reason=_candidate_status_reason(
                "fee", fee_statuses[_table_candidate_id(table)]
            ),
            evidence_refs=_refs(table, chunk_map),
        )
        for table in fee_candidates
    ]
    performance_candidates = [
        table for table in tables if table.section_type == SectionType.PERFORMANCE
    ]
    candidate_outcomes.extend(
        CandidateOutcome(
            field="performance",
            owner="table",
            candidate_id=_table_candidate_id(table),
            status=_performance_candidate_status(table),
            reason=_candidate_status_reason(
                "performance", _performance_candidate_status(table)
            ),
            evidence_refs=_refs(table, chunk_map),
        )
        for table in performance_candidates
    )
    return LLMExtractionResult(
        fees=fees,
        performance=performance,
        ownership=ownership,
        candidate_outcomes=candidate_outcomes,
    )


def apply_table_facts(
    product: CanonicalProduct,
    tables: list[DetectedTable] | None,
    chunks: list[Chunk] | None = None,
) -> CanonicalProduct:
    extracted = extract_table_facts(tables, chunks)
    for outcome in extracted.ownership:
        product.extraction.ownership = [
            item
            for item in product.extraction.ownership
            if not (item.field == outcome.field and item.owner == outcome.owner)
        ]
        product.extraction.ownership.append(outcome)
    product.extraction.candidate_outcomes = [
        item
        for item in product.extraction.candidate_outcomes
        if item.owner != "table" or item.field not in {"fees", "performance"}
    ]
    product.extraction.candidate_outcomes.extend(extracted.candidate_outcomes)
    if extracted.fees:
        product.fees = _merge_fees(extracted.fees, [])
    if extracted.performance:
        # Structured performance tables are authoritative; do not keep stale
        # rows previously bound to mixed OTHER evidence.
        product.performance = _merge_performance(extracted.performance, [])
    return product


def _summary_pages(tables: list[DetectedTable], section: SectionType) -> set[int] | None:
    pages = sorted(
        {
            table.page_number
            for table in tables
            if table.section_type == section
            and table.extraction_method in STRUCTURED_METHODS
            and table.rows
        }
    )
    if not pages:
        return None
    start = pages[0]
    return {start, start + 1}


def _first_valid_pages(tables: list[DetectedTable]) -> set[int] | None:
    if not tables:
        return None
    start = min(table.page_number for table in tables)
    return {start, start + 1}


def _iter_structured(
    tables: list[DetectedTable],
    section: SectionType,
    pages: set[int] | None,
):
    for table in tables:
        if table.section_type != section:
            continue
        if table.extraction_method not in STRUCTURED_METHODS:
            continue
        if pages is not None and table.page_number not in pages:
            continue
        yield table


def _refs(table: DetectedTable, chunk_map: dict[str, str]) -> list[str]:
    chunk_id = chunk_map.get(table.table_id)
    return [chunk_id] if chunk_id else []


def _table_candidate_id(table: DetectedTable) -> str:
    return table.table_id or f"page:{table.page_number}:{table.section_type.value}"


def _fees_from_summary_chunks(
    chunks: list[Chunk],
    chunk_map: dict[str, str],
) -> list[FeeItem]:
    """Recover single-share fee rows from summary 투자비용 text blocks."""
    pattern = re.compile(
        r"투자신탁\s*(없음|-)?\s*"
        r"(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)",
        flags=re.S,
    )
    items: list[FeeItem] = []
    for chunk in chunks:
        text = chunk.text or ""
        if "총보수" not in text or "투자신탁" not in text:
            continue
        if chunk.page_start > 8 and chunk.section_type != SectionType.FEES:
            continue
        match = pattern.search(re.sub(r"[ \t]+", "", text))
        if not match:
            # Keep spaces for the spaced prospectus layout.
            match = pattern.search(text)
        if not match:
            continue
        refs = [chunk.chunk_id]
        sales = None if match.group(1) in {None, "없음", "-"} else None
        rates = {
            "total_fee": float(match.group(2)),
            "sales_remuneration": float(match.group(3)),
            "peer_group_total_fee": float(match.group(4)),
            "total_fee_and_expenses": float(match.group(5)),
        }
        items.append(
            FeeItem(
                class_name="투자신탁",
                fee_type="sales_fee",
                rate=sales,
                unit="%",
                condition="없음",
                evidence_refs=list(refs),
                column_name="sales_fee",
                raw_cell_text="없음",
            )
        )
        for fee_type, rate in rates.items():
            if rate >= 10:
                continue
            items.append(
                FeeItem(
                    class_name="투자신탁",
                    fee_type=fee_type,
                    rate=rate,
                    unit="%",
                    evidence_refs=list(refs),
                    column_name=fee_type,
                    raw_cell_text=str(rate),
                )
            )
        break
    return items


def _fees_from_row(
    row: list[str],
    refs: list[str],
    resolver: ClassResolver | None = None,
    headers: list[str] | None = None,
    table_rows: list[list[str]] | None = None,
    row_index: int | None = None,
) -> list[FeeItem]:
    if len(row) < 3:
        return []
    class_name = resolver.resolve(row[0]) if resolver else normalize_class_name(row[0])
    if not _is_fee_class_subject(class_name or row[0]) or _contains_date_or_period(row[1]):
        return []
    if not class_name and _compact_subject(row[0]) == "투자신탁":
        class_name = "투자신탁"
    header_items = _fees_from_header_row(class_name, row, refs, headers or [], row_index)
    if header_items is not None:
        return header_items
    if _has_investment_cost_columns(headers or [], table_rows or []):
        # Some summary tables put fee columns and the 1/2/3/5/10-year cost
        # examples in one physical row. Only the four cells before the cost
        # examples are rates; values such as 57/116 are KRW-thousand costs.
        sales = _sales_fee(class_name, row[1], refs, row_index=row_index)
        items = [sales] if sales else []
        for fee_type, raw in zip(
            ("total_fee", "sales_remuneration", "peer_group_total_fee"),
            row[2:5],
            strict=False,
        ):
            rate = _parse_rate(raw)
            if rate is None:
                continue
            items.append(FeeItem(
                class_name=class_name,
                fee_type=fee_type,
                rate=rate,
                unit="%",
                evidence_refs=list(refs),
                row_index=row_index,
                column_name=fee_type,
                raw_cell_text=raw,
            ))
        return items
    sales_raw, values = _split_fee_cells(row[1:])
    if len(values) < 3:
        return []
    items: list[FeeItem] = []
    sales = _sales_fee(class_name, sales_raw, refs, row_index=row_index)
    if sales:
        items.append(sales)
    for fee_type, raw in zip(FEE_TYPES[1:], values[:4], strict=False):
        rate = _parse_rate(raw)
        if rate is None and not raw:
            continue
        if raw in {"-", "없음", "해당없음"}:
            continue
        if rate is not None and rate >= 10:
            continue
        items.append(
            FeeItem(
                class_name=class_name,
                fee_type=fee_type,
                rate=rate,
                unit="%",
                evidence_refs=list(refs),
                row_index=row_index,
                column_name=fee_type,
                raw_cell_text=raw,
            )
        )
    return items


def _fees_from_header_row(
    class_name: str,
    row: list[str],
    refs: list[str],
    headers: list[str],
    row_index: int | None = None,
) -> list[FeeItem] | None:
    normalized = [re.sub(r"[\sㆍ·]+", "", header or "") for header in headers]
    expected = {
        "판매수수료": "sales_fee",
        "총보수": "total_fee",
        "판매보수": "sales_remuneration",
        "동종유형총보수": "peer_group_total_fee",
        "총보수비용": "total_fee_and_expenses",
    }
    indexes: dict[str, int] = {}
    for index, header in enumerate(normalized):
        fee_type = expected.get(header)
        if fee_type:
            indexes[fee_type] = index
    if not {"total_fee", "sales_remuneration"}.issubset(indexes):
        return None
    items: list[FeeItem] = []
    for fee_type, index in indexes.items():
        raw = row[index] if index < len(row) else ""
        if fee_type == "sales_fee":
            item = _sales_fee(class_name, raw, refs, row_index=row_index)
            if item:
                items.append(item)
            continue
        rate = _parse_rate(raw)
        if rate is None:
            continue
        # 1년/2년… 투자비용 예시(단위:천원) 열이 헤더 매핑에 섞이면 54·93 같은
        # 원화 비용이 % 수수료로 잡힌다. 비율 수수료는 10% 미만만 허용.
        if rate >= 10:
            continue
        items.append(
            FeeItem(
                class_name=class_name,
                fee_type=fee_type,
                rate=rate,
                unit="%",
                evidence_refs=list(refs),
                row_index=row_index,
                column_name=fee_type,
                raw_cell_text=raw,
            )
        )
    return items


def _split_fee_cells(cells: list[str]) -> tuple[str, list[str]]:
    remaining = list(cells)
    sales_raw = remaining.pop(0).strip() if remaining else ""
    if sales_raw in {"-", "없음", "해당없음"} and remaining:
        duplicate_empty = remaining[0].strip()
        if duplicate_empty in {"-", "없음", "해당없음"}:
            remaining.pop(0)
    values: list[str] = []
    for cell in remaining:
        text = (cell or "").strip()
        if _parse_rate(text) is None:
            continue
        values.append(text)
        if len(values) == 4:
            break
    return sales_raw, values


def _has_investment_cost_columns(headers: list[str], rows: list[list[str]]) -> bool:
    context = " ".join(headers + [cell for row in rows[:12] for cell in row])
    compact = re.sub(r"\s+", "", context)
    return (
        "투자기간별총비용예시" in compact
        and "단위:천원" in compact
        and "1년" in compact
        and "2년" in compact
    )


def _contains_date_or_period(value: str | None) -> bool:
    compact = re.sub(r"\s+", "", value or "")
    return bool(
        re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", compact)
        or ("~" in compact and re.search(r"\d{2,4}[-/.]\d{1,2}", compact))
    )


def _looks_like_fee_table(table: DetectedTable) -> bool:
    blob = re.sub(
        r"\s+",
        "",
        " ".join(table.headers + [cell for row in table.rows[:12] for cell in row]),
    )
    if "투자실적" in blob or "연평균수익률" in blob:
        return False
    class_rows = sum(
        1
        for row in table.rows
        if row and _is_fee_class_subject(normalize_class_name(row[0]) or row[0])
    )
    return (
        table.section_type == SectionType.FEES
        or ("총보수" in blob and ("판매수수료" in blob or class_rows >= 1))
    )


def _compact_subject(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _is_fee_class_subject(name: str | None) -> bool:
    if is_plausible_class_name(name):
        return True
    # Single-share prospectuses label the sole fee subject as 투자신탁.
    return _compact_subject(name) == "투자신탁"


def _normalize_extractable_tables(tables: list[DetectedTable]) -> list[DetectedTable]:
    """Recover PERFORMANCE/FEES tables that remained as raw pymupdf/OTHER."""
    recovered: list[DetectedTable] = []
    seen_ids: set[str] = set()
    for table in tables:
        rows = [list(table.headers), *[list(row) for row in table.rows]]
        blob = re.sub(
            r"\s+",
            "",
            " ".join(table.headers + [cell for row in table.rows[:12] for cell in row]),
        )
        fee_signal = "총보수" in blob and any(
            token in blob for token in ("판매수수료", "판매보수", "동종유형", "투자신탁", "판매회사보수")
        )
        perf_signal = any(
            token in blob
            for token in ("최근1년", "연평균수익률", "설정일이후", "수익률변동성", "비교지수")
        )
        # Fee tables that also mention rates must not be rewritten as performance
        # just because a numeric row resembles a return series.
        if fee_signal and (
            table.section_type in {SectionType.FEES, SectionType.OTHER}
            or table.extraction_method == "pymupdf"
        ):
            rebuilt_fee = reconstruct_fee_table(
                rows, f"{table.table_id}_fee", table.page_number
            )
            if rebuilt_fee and rebuilt_fee.rows:
                rebuilt_fee.table_id = table.table_id
                recovered.append(rebuilt_fee)
                seen_ids.add(table.table_id)
                continue
        if (
            (perf_signal or table.section_type == SectionType.PERFORMANCE)
            and not fee_signal
            and (
                table.section_type in {SectionType.PERFORMANCE, SectionType.OTHER}
                or table.extraction_method == "pymupdf"
            )
        ):
            rebuilt = reconstruct_performance_table(
                rows, f"{table.table_id}_perf", table.page_number
            )
            if rebuilt and rebuilt.rows:
                rebuilt.table_id = table.table_id
                recovered.append(rebuilt)
                seen_ids.add(table.table_id)
                continue
        if table.section_type in {SectionType.FEES, SectionType.OTHER} or (
            table.extraction_method == "pymupdf"
        ):
            rebuilt_fee = reconstruct_fee_table(
                rows, f"{table.table_id}_fee", table.page_number
            )
            if rebuilt_fee and rebuilt_fee.rows:
                rebuilt_fee.table_id = table.table_id
                recovered.append(rebuilt_fee)
                seen_ids.add(table.table_id)
                continue
        if table.table_id not in seen_ids:
            recovered.append(table)
            seen_ids.add(table.table_id)
    return recovered


def _promote_fee_sections(
    valid_fee_tables: list[DetectedTable],
    chunks: list[Chunk] | None,
) -> None:
    """VALUE_CORRECT_SECTION_WRONG: canonicalize evidence section to FEES."""
    for table in valid_fee_tables:
        table.section_type = SectionType.FEES
        for chunk in chunks or []:
            if chunk.table_id == table.table_id:
                chunk.section_type = SectionType.FEES
                # Keep chunk_id stable; section metadata is what validators check.
                if "_other_" in chunk.chunk_id:
                    # Do not rename chunk_id (refs already bound); only section_type.
                    pass


def _fee_table_status(table: DetectedTable, resolver: ClassResolver) -> str:
    if not table.rows:
        return "REJECTED"
    resolved_rows = [row for row in table.rows if row and resolver.resolve(row[0])]
    if not resolved_rows:
        return "AMBIGUOUS"
    valid_rows = [
        row for row in resolved_rows if _fees_from_row(row, [], resolver, table.headers, table.rows)
    ]
    if valid_rows:
        return "VALID"
    if any(len(row) > 1 and _contains_date_or_period(row[1]) for row in resolved_rows):
        return "REJECTED"
    return "AMBIGUOUS"


def _aggregate_table_status(
    candidates: list[DetectedTable],
    valid_tables: list[DetectedTable],
    items: list,
) -> str:
    if items and valid_tables:
        return "VALID"
    # Summary-text fee recovery (e.g. single-share 투자신탁 rows) can populate
    # source-backed items without a VALID structured table candidate.
    if items and all(getattr(item, "evidence_refs", None) for item in items):
        return "VALID"
    if not candidates:
        return "NOT_FOUND"
    if any(assess_table(table) == BROKEN for table in candidates):
        return "REJECTED"
    return "AMBIGUOUS"


def _performance_status(
    tables: list[DetectedTable],
    performance: list[PerformanceItem],
) -> str:
    if performance:
        return "VALID"
    candidates = [table for table in tables if table.section_type == SectionType.PERFORMANCE]
    if not candidates:
        return "NOT_FOUND"
    if all(assess_table(table) == BROKEN for table in candidates):
        return "REJECTED"
    return "AMBIGUOUS"


def _performance_candidate_status(table: DetectedTable) -> str:
    if not table.rows or assess_table(table) == BROKEN:
        return "REJECTED"
    for row in table.rows:
        kind = _performance_kind(row[0] if row else "")
        if kind != "unlabeled" and _performance_from_row(row, [], kind):
            return "VALID"
    return "AMBIGUOUS"


def _candidate_status_reason(kind: str, status: str) -> str:
    if status == "VALID":
        return f"{kind.title()} candidate passed semantic validation."
    if status == "REJECTED":
        return f"{kind.title()} candidate failed semantic validation."
    return f"{kind.title()} candidate could not be mapped unambiguously."


def _table_status_reason(
    kind: str,
    candidates: list[DetectedTable],
    valid_tables: list[DetectedTable],
    items: list,
) -> str:
    status = _aggregate_table_status(candidates, valid_tables, items)
    if status == "VALID":
        return f"Validated {kind} table rows are authoritative."
    if status == "NOT_FOUND":
        return f"No {kind} table candidate was found."
    if status == "REJECTED":
        return f"{kind.title()} table candidates failed semantic validation."
    return f"{kind.title()} table candidates were present but column mapping was ambiguous."


def _sales_fee(
    class_name: str,
    raw: str,
    refs: list[str],
    *,
    row_index: int | None = None,
) -> FeeItem | None:
    text = (raw or "").strip()
    if not text:
        return None
    rate = _parse_rate(text)
    if rate is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        rate = float(match.group(1)) if match else None
    condition = text if (not _is_number_token(text) or "납입" in text or "%" in text) else None
    if text in {"-", "없음", "해당없음", "해당사항 없음"}:
        return FeeItem(
            class_name=class_name,
            fee_type="sales_fee",
            rate=None,
            unit="%",
            condition=text,
            evidence_refs=list(refs),
            row_index=row_index,
            column_name="sales_fee",
            raw_cell_text=raw,
        )
    if rate is None and not condition:
        return None
    return FeeItem(
        class_name=class_name,
        fee_type="sales_fee",
        rate=rate,
        unit="%",
        condition=condition,
        evidence_refs=list(refs),
        row_index=row_index,
        column_name="sales_fee",
        raw_cell_text=raw,
    )


def _performance_kind(name: str) -> str:
    compact = (name or "").replace(" ", "")
    if DATE_NAME_RE.match(compact.replace(".", "-").replace("/", "-")):
        return "unlabeled"
    if "비교지수" in compact:
        return "benchmark"
    if "변동성" in compact:
        return "volatility"
    if any(token in compact for token in FUND_AGGREGATE_NAMES):
        return "aggregate"
    if is_plausible_class_name(name) or "수수료" in compact or compact.startswith("종류"):
        return "class"
    return "aggregate"


def _performance_from_row(
    row: list[str], refs: list[str], kind: str, row_index: int | None = None
) -> list[PerformanceItem]:
    if len(row) < 3:
        return []
    name = row[0]
    class_name = normalize_class_name(name) if kind == "class" else None
    subject = {
        "benchmark": "비교지수",
        "volatility": "수익률 변동성",
        "aggregate": "투자신탁",
        "class": class_name or name,
    }[kind]
    metric = {
        "benchmark": "benchmark_return",
        "volatility": "volatility",
        "aggregate": "fund_return",
        "class": "fund_return",
    }[kind]
    items: list[PerformanceItem] = []
    for period, raw in zip(PERIODS, row[2:7], strict=False):
        rate = _parse_rate(raw)
        if rate is None:
            continue
        items.append(
            PerformanceItem(
                class_name=class_name,
                subject=subject,
                metric_type=metric,
                period=period,
                return_rate=rate,
                unit="%",
                evidence_refs=list(refs),
                row_index=row_index,
                column_name=period,
                raw_cell_text=raw,
            )
        )
    return items


def _merge_fees(table_fees: list[FeeItem], existing: list[FeeItem]) -> list[FeeItem]:
    ordered: list[FeeItem] = []
    index: dict[tuple, int] = {}
    for item in table_fees + existing:
        key = (class_identity(item.class_name), item.fee_type, item.as_of_date)
        if key in index:
            current = ordered[index[key]]
            current.class_name = prefer_class_name(current.class_name, item.class_name)
            continue
        index[key] = len(ordered)
        ordered.append(item)
    return ordered


def _merge_performance(table_rows: list[PerformanceItem], existing: list[PerformanceItem]) -> list[PerformanceItem]:
    has_class_return = any(
        item.class_name and item.metric_type == "fund_return" for item in table_rows
    )
    if has_class_return:
        existing = [
            item
            for item in existing
            if not (
                item.metric_type == "fund_return"
                and not item.class_name
                and (item.subject or "") not in {"비교지수", "수익률 변동성"}
            )
        ]
    ordered: list[PerformanceItem] = []
    index: dict[tuple, int] = {}
    for item in table_rows + existing:
        key = _perf_key(item)
        if key in index:
            current = ordered[index[key]]
            current.class_name = prefer_class_name(current.class_name, item.class_name)
            continue
        index[key] = len(ordered)
        ordered.append(item)
    return ordered


def _perf_key(item: PerformanceItem) -> tuple:
    identity = class_identity(item.class_name) if item.class_name else (item.metric_type or item.subject)
    return (identity, item.metric_type, item.period, item.as_of_date)


def _parse_rate(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if text in {"-", "없음", "해당없음"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_number_token(text: str) -> bool:
    try:
        float(text.replace(",", ""))
        return True
    except ValueError:
        return False
