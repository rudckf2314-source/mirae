from __future__ import annotations

import re

from processing.class_candidates import class_code, class_identity
from schemas.chunk import Chunk, SectionType
from schemas.document import DetectedTable
from schemas.product import FeeItem, PerformanceItem, VerificationItem
from verification.text import approx_equal, compact, format_value, looks_like_date, parse_number

FEE_COLUMNS = {
    "sales_fee": 1,
    "total_fee": 2,
    "sales_remuneration": 3,
    "peer_group_total_fee": 4,
    "total_fee_and_expenses": 5,
}
PERIOD_COLUMNS = {
    "1Y": 2,
    "2Y": 3,
    "3Y": 4,
    "5Y": 5,
    "SINCE_INCEPTION": 6,
}


def _performance_period_column(table: DetectedTable, period: str | None) -> int | None:
    """Resolve period column from headers when inception/date column is absent."""
    if not period:
        return None
    headers = [compact(header) for header in table.headers]
    mapped: dict[str, int] = {}
    for index, header in enumerate(headers):
        if "1년" in header:
            mapped["1Y"] = index
        elif "2년" in header:
            mapped["2Y"] = index
        elif "3년" in header:
            mapped["3Y"] = index
        elif "5년" in header:
            mapped["5Y"] = index
        elif "설정일이후" in header:
            mapped["SINCE_INCEPTION"] = index
    if period in mapped:
        # Header indexes are authoritative only when the row shape matches the
        # header width. Rows that still carry an inception date in column 1
        # keep the standard [name, inception, periods...] offsets.
        sample = next((row for row in table.rows if row and len(row) > 1), None)
        if sample is not None and looks_like_date(sample[1]) and mapped[period] == PERIOD_COLUMNS.get(period, mapped[period]) - 1:
            return PERIOD_COLUMNS.get(period)
        return mapped[period]
    base = PERIOD_COLUMNS.get(period)
    if base is None:
        return None
    has_inception = any("최초설정" in header for header in headers)
    date_like = sum(1 for row in table.rows[:8] if row and len(row) > 1 and looks_like_date(row[1]))
    if not has_inception and date_like == 0 and base > 0:
        return base - 1
    return base
PERFORMANCE_HEADER_MARKERS = ("최근", "설정일", "연평균", "1년", "2년", "3년", "5년")
FEE_HEADER_MARKERS = ("총보수", "판매수수료", "판매보수", "투자비용")
FEE_TEXT_CLASS_RE = re.compile(
    r"(?:수수료(?:선취|후취|미징구)[가-힣A-Za-z0-9,\-]*\([A-Za-z][A-Za-z0-9\-]*\)"
    r"|[A-Za-z][A-Za-z0-9\-]*\(수수료(?:선취|후취|미징구)[가-힣A-Za-z0-9,\-]*\))"
)
FEE_TEXT_SEQUENCE = (
    "sales_fee",
    "total_fee",
    "sales_remuneration",
    "peer_group_total_fee",
    "total_fee_and_expenses",
)
FEE_TEXT_LABELS = {
    "sales_fee": ("판매수수료", "납입금액의"),
    "total_fee": ("총보수",),
    "sales_remuneration": ("판매보수",),
    "peer_group_total_fee": ("동종유형",),
    "total_fee_and_expenses": ("총보수·비용", "총보수ㆍ비용", "총보수비용"),
}


def verify_fee(
    field_path: str,
    item: FeeItem,
    chunks: list[Chunk],
    tables: list[DetectedTable],
) -> VerificationItem:
    refs = list(item.evidence_refs or [])
    base = {
        "field_path": field_path,
        "method": "table_cell",
        "extracted_value": format_value(item.rate) if item.rate is not None else item.condition,
        "evidence_refs": refs,
    }
    if not refs:
        return VerificationItem(status="SKIPPED", verdict="UNVERIFIABLE", reason="evidence_ref가 없습니다.", **base)
    table, table_kind = _resolve_table(refs, chunks, tables)
    prefer_page = table.page_number if table is not None else None
    performance_bound = bool(
        table is not None
        and (table_kind == "performance" or _looks_like_performance(table))
    )

    # Mixed summary pages bind fees to an OTHER/PERFORMANCE mega-table. Prefer a
    # class row whose fee cell actually supports the extracted rate — including
    # OTHER summary fee rows on the same page.
    support = _find_supporting_fee_row(tables, item, prefer_page)
    if support is not None:
        table, row, row_index, col, cell = support
        return _compare_numeric_cell(
            base,
            expected=item.rate,
            cell=cell,
            row=row,
            col=col,
            location=f"row={row_index} column={col} ({item.fee_type})",
            blank_ok=item.rate is None,
            blank_tokens=("-", "없음", "해당없음", item.condition or ""),
            allow_same_row_realignment=False,
        )

    if performance_bound:
        text_result = _verify_fee_from_text(base, item, refs, chunks)
        if text_result.status in {"PASS", "WARNING"}:
            return text_result
        return VerificationItem(
            status="FAIL",
            verdict="CONTRADICTED",
            reason="수수료 값이 수익률 표(최근 n년/설정일) 근거에 매핑되어 있습니다.",
            **base,
        )

    if table is None:
        return _verify_fee_from_text(base, item, refs, chunks)

    row, row_index = _find_class_row(table, item.class_name)
    if row is None:
        text_result = _verify_fee_from_text(base, item, refs, chunks)
        if text_result.status in {"PASS", "WARNING"}:
            return text_result
        return VerificationItem(
            status="FAIL",
            verdict="UNSUPPORTED",
            reason=f"표에서 클래스 행을 찾지 못했습니다: {item.class_name}",
            **base,
        )
    row = _normalize_fee_row(row)
    col = FEE_COLUMNS.get(item.fee_type or "")
    if col is None or col >= len(row):
        return VerificationItem(
            status="FAIL",
            verdict="UNSUPPORTED",
            reason=f"표에서 {item.fee_type} 열을 확인하지 못했습니다.",
            **base,
        )
    cell = _fee_cell_for_compare(row, col, item)
    result = _compare_numeric_cell(
        base,
        expected=item.rate,
        cell=cell,
        row=row,
        col=col,
        location=f"row={row_index} column={col} ({item.fee_type})",
        blank_ok=item.rate is None,
        blank_tokens=("-", "없음", "해당없음", item.condition or ""),
    )
    if result.status == "PASS":
        return result
    text_result = _verify_fee_from_text(base, item, refs, chunks)
    if text_result.status in {"PASS", "WARNING"}:
        return text_result
    return result


def _normalize_fee_row(row: list[str]) -> list[str]:
    normalized = list(row)
    if len(normalized) > 2:
        first = compact(normalized[1])
        second = compact(normalized[2])
        empty_tokens = {"-", "없음", "해당없음", "해당사항없음"}
        if first in empty_tokens and second in empty_tokens:
            normalized.pop(2)
    return normalized


def verify_performance(
    field_path: str,
    item: PerformanceItem,
    chunks: list[Chunk],
    tables: list[DetectedTable],
) -> VerificationItem:
    refs = list(item.evidence_refs or [])
    base = {
        "field_path": field_path,
        "method": "table_cell",
        "extracted_value": format_value(item.return_rate),
        "evidence_refs": refs,
    }
    if not refs:
        return VerificationItem(status="SKIPPED", verdict="UNVERIFIABLE", reason="evidence_ref가 없습니다.", **base)
    table, table_kind = _resolve_table(refs, chunks, tables)
    if table is None:
        return VerificationItem(status="SKIPPED", verdict="UNVERIFIABLE", reason="연결된 표를 찾지 못했습니다.", **base)
    if table_kind == "fees" or (_looks_like_fee(table) and not _looks_like_performance(table)):
        return VerificationItem(
            status="FAIL",
            verdict="CONTRADICTED",
            reason="수익률 값이 보수/수수료 표 근거에 매핑되어 있습니다.",
            **base,
        )
    row, row_index = _find_performance_row(table, item)
    if row is None:
        return _verify_unlabeled_performance(base, table, item)
    col = _performance_period_column(table, item.period)
    if col is None or col >= len(row):
        return VerificationItem(
            status="FAIL",
            verdict="UNSUPPORTED",
            reason=f"표에서 {item.period} 열을 확인하지 못했습니다.",
            **base,
        )
    return _compare_numeric_cell(
        base,
        expected=item.return_rate,
        cell=row[col],
        row=row,
        col=col,
        location=f"row={row_index} column={col} ({item.period})",
        blank_ok=False,
        blank_tokens=(),
        allow_same_row_realignment=(
            item.metric_type in {"benchmark_return", "volatility"}
            or looks_like_date(row[col] if col < len(row) else "")
            or compact(row[col] if col < len(row) else "") in {"-", "–", "—"}
        ),
    )


def _compare_numeric_cell(
    base: dict,
    expected: float | None,
    cell: str,
    row: list[str],
    col: int,
    location: str,
    blank_ok: bool,
    blank_tokens: tuple[str, ...],
    allow_same_row_realignment: bool = False,
) -> VerificationItem:
    cell_number = parse_number(cell)
    compact_cell = compact(cell)
    if expected is None:
        if blank_ok and (
            not compact_cell
            or any(compact(token) and compact(token) in compact_cell for token in blank_tokens if token)
        ):
            return VerificationItem(status="PASS", verdict="SUPPORTED", reason=location, **base)
        return VerificationItem(
            status="FAIL",
            verdict="UNSUPPORTED",
            reason=f"{location} 셀 값이 비어 있어야 하는데 '{cell}'입니다.",
            **base,
        )
    if approx_equal(cell_number, expected):
        return VerificationItem(status="PASS", verdict="SUPPORTED", reason=location, **base)
    # Deferred/conditional sales-fee cells store the rate inside a phrase such as
    # "3년 미만 환매금액의 0.15% 이내" (parse_number may latch onto "3").
    if (
        expected is not None
        and any(token in (cell or "") for token in ("%", "이내", "환매", "납입"))
        and _rate_in_text(cell or "", expected)
    ):
        return VerificationItem(
            status="PASS",
            verdict="SUPPORTED",
            reason=f"{location} 조건 문구에서 {expected}가 확인됩니다.",
            **base,
        )
    other_cols = [
        index
        for index, other in enumerate(row)
        if index != col and approx_equal(parse_number(other), expected)
    ]
    if other_cols:
        if allow_same_row_realignment:
            return VerificationItem(
                status="PASS",
                verdict="SUPPORTED",
                reason=f"{location} 같은 행의 열 {other_cols[0]}에서 {expected}가 확인됩니다.",
                **base,
            )
        return VerificationItem(
            status="FAIL",
            verdict="CONTRADICTED",
            reason=f"{location}의 셀은 '{cell}'인데, 같은 행의 다른 열 {other_cols}에서 {expected}가 발견되었습니다.",
            **base,
        )
    return VerificationItem(
        status="FAIL",
        verdict="CONTRADICTED",
        reason=f"{location} 셀 '{cell}'이 추출값 {expected}와 일치하지 않습니다.",
        **base,
    )


def _resolve_table(
    refs: list[str],
    chunks: list[Chunk],
    tables: list[DetectedTable],
) -> tuple[DetectedTable | None, str | None]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    table_map = {table.table_id: table for table in tables}
    for ref in refs:
        chunk = chunk_map.get(ref)
        if not chunk:
            continue
        if chunk.table_id and chunk.table_id in table_map:
            table = table_map[chunk.table_id]
            return table, _table_kind(table)
        if chunk.rows:
            synthetic = DetectedTable(
                table_id=chunk.table_id or chunk.chunk_id,
                page_number=chunk.page_start,
                section_type=chunk.section_type,
                headers=list(chunk.headers),
                rows=[list(row) for row in chunk.rows],
            )
            return synthetic, _table_kind(synthetic)
    return None, None


def _table_kind(table: DetectedTable) -> str:
    if table.section_type == SectionType.PERFORMANCE or _looks_like_performance(table):
        return "performance"
    if table.section_type == SectionType.FEES or _looks_like_fee(table):
        return "fees"
    return "other"


def _looks_like_performance(table: DetectedTable) -> bool:
    blob = compact(" ".join(table.headers))
    if any(compact(marker) in blob for marker in PERFORMANCE_HEADER_MARKERS):
        return True
    for row in table.rows[:3]:
        if len(row) > 1 and looks_like_date(row[1]):
            return True
    return False


def _looks_like_fee(table: DetectedTable) -> bool:
    blob = compact(" ".join(table.headers))
    return any(compact(marker) in blob for marker in FEE_HEADER_MARKERS)


def _fee_cell_for_compare(row: list[str], col: int, item: FeeItem) -> str:
    cell = row[col] if col < len(row) else ""
    # Sparse peer-group cells sometimes leave the numeric in the next column.
    if (
        item.fee_type == "peer_group_total_fee"
        and item.rate is not None
        and not (cell or "").strip()
        and col + 1 < len(row)
        and parse_number(row[col + 1]) is not None
        and approx_equal(parse_number(row[col + 1]), item.rate)
    ):
        return row[col + 1]
    return cell


def _cell_supports_fee_rate(cell: str, expected: float | None, row: list[str], col: int) -> bool:
    if expected is None:
        compact_cell = compact(cell)
        return (not compact_cell) or compact_cell in {"-", "없음", "해당없음", "해당사항없음"}
    if approx_equal(parse_number(cell), expected):
        return True
    if any(token in (cell or "") for token in ("%", "이내", "환매", "납입")) and _rate_in_text(
        cell or "", expected
    ):
        return True
    return False


def _row_looks_like_fee_rates(row: list[str]) -> bool:
    if len(row) < 3:
        return False
    rate_hits = 0
    for cell in row[1:6]:
        text = cell or ""
        if any(token in text for token in ("%", "이내", "납입", "환매", "없음")):
            rate_hits += 1
            continue
        number = parse_number(text)
        if number is not None and 0 <= number < 10:
            rate_hits += 1
    return rate_hits >= 2


def _find_supporting_fee_row(
    tables: list[DetectedTable],
    item: FeeItem,
    page_number: int | None,
) -> tuple[DetectedTable, list[str], int, int, str] | None:
    """Locate a class fee row whose cell supports the extracted rate."""
    col = FEE_COLUMNS.get(item.fee_type or "")
    if col is None:
        return None
    candidates: list[tuple[tuple, DetectedTable, list[str], int, str]] = []
    for table in tables:
        row, row_index = _find_class_row(table, item.class_name)
        if row is None or row_index is None:
            continue
        if not (
            table.section_type == SectionType.FEES
            or _looks_like_fee(table)
            or _row_looks_like_fee_rates(row)
        ):
            continue
        # Pure PERFORMANCE section tables are never fee support. Mixed OTHER
        # summary tables may look performance-like yet still carry fee rows.
        if table.section_type == SectionType.PERFORMANCE and not _looks_like_fee(table):
            continue
        if (
            _looks_like_performance(table)
            and not _looks_like_fee(table)
            and not _row_looks_like_fee_rates(row)
        ):
            continue
        normalized = _normalize_fee_row(row)
        if col >= len(normalized):
            continue
        cell = _fee_cell_for_compare(normalized, col, item)
        if not _cell_supports_fee_rate(cell, item.rate, normalized, col):
            continue
        page_distance = abs(table.page_number - page_number) if page_number is not None else 0
        rank = (
            0 if approx_equal(parse_number(cell), item.rate) or (
                item.rate is not None and _rate_in_text(cell or "", item.rate)
            ) else 1,
            0 if table.section_type == SectionType.FEES else 1,
            0 if _looks_like_fee(table) else 1,
            page_distance,
            -len(normalized),
        )
        candidates.append((rank, table, normalized, row_index, cell))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, table, row, row_index, cell = candidates[0]
    return table, row, row_index, col, cell


def _preferred_fee_table(
    tables: list[DetectedTable],
    class_name: str | None,
    page_number: int | None,
) -> DetectedTable | None:
    """Pick a structured fee table that actually contains the class row."""
    candidates: list[DetectedTable] = []
    for table in tables:
        row, _ = _find_class_row(table, class_name)
        if row is None:
            continue
        if not (
            table.section_type == SectionType.FEES
            or _looks_like_fee(table)
            or _row_looks_like_fee_rates(row)
        ):
            continue
        if _looks_like_performance(table) and not _looks_like_fee(table) and not _row_looks_like_fee_rates(row):
            continue
        if page_number is not None and abs(table.page_number - page_number) > 2:
            continue
        candidates.append(table)
    if not candidates:
        # Widen to any document fee table with the class when nearby pages miss it.
        for table in tables:
            row, _ = _find_class_row(table, class_name)
            if row is None:
                continue
            if table.section_type == SectionType.FEES or _looks_like_fee(table) or _row_looks_like_fee_rates(row):
                candidates.append(table)
    if not candidates:
        return None
    method_rank = {
        "text_fallback": 0,
        "pymupdf_normalized": 1,
        "pdfplumber": 2,
        "pdfminer_coordinate_fallback": 3,
        "pymupdf": 9,
    }
    return sorted(
        candidates,
        key=lambda table: (
            0 if table.section_type == SectionType.FEES else 1,
            0 if _looks_like_fee(table) else 1,
            method_rank.get(table.extraction_method, 8),
            abs(table.page_number - (page_number or table.page_number)),
            -len(table.rows),
        ),
    )[0]


def _class_identity_aliases(name: str | None) -> set[str]:
    ident = class_identity(name)
    if not ident:
        return set()
    aliases = {ident}
    # 보수체감 series markers: 종류C-0 / 종류C-e-0 ↔ table labels 종류C / 종류C-e
    trimmed = re.sub(r"-\d+$", "", ident)
    if trimmed and trimmed != ident:
        aliases.add(trimmed)
    return aliases


def _find_class_row(table: DetectedTable, class_name: str | None) -> tuple[list[str] | None, int | None]:
    targets = _class_identity_aliases(class_name)
    code = class_code(class_name)
    target_stem = re.sub(r"\([A-Za-z][A-Za-z0-9\-]*\)$", "", compact(class_name or ""))
    if not targets and not target_stem:
        return None, None
    for index, row in enumerate(table.rows):
        if not row:
            continue
        label = row[0]
        if _class_identity_aliases(label) & targets:
            return row, index
        # Performance tables often keep code and kind in adjacent cells.
        if len(row) > 1 and _class_identity_aliases(f"{row[1]}({row[0]})") & targets:
            return _normalize_performance_class_row(table, row[0], row[1], row[2:]), index
        # Stem matching is only safe when the extracted label has no concrete class
        # code (A2) — series markers like C-0 are handled via identity aliases.
        if code and not re.search(r"-\d+$", code):
            continue
        label_stem = re.sub(r"\([A-Za-z][A-Za-z0-9\-]*\)$", "", compact(label))
        if target_stem and label_stem and target_stem == label_stem:
            return row, index
        if (
            len(row) > 1
            and target_stem
            and re.sub(r"\([A-Za-z][A-Za-z0-9\-]*\)$", "", compact(row[1])) == target_stem
        ):
            return _normalize_performance_class_row(table, row[0], row[1], row[2:]), index
    return None, None


def _normalize_performance_class_row(
    table: DetectedTable,
    code: str,
    kind: str,
    rates: list[str],
) -> list[str]:
    label = f"{kind}({code})" if code and kind else (kind or code)
    has_inception = any("최초설정" in compact(header) for header in table.headers)
    if has_inception:
        return [label, "", *rates]
    return [label, *rates]


def _find_performance_row(table: DetectedTable, item: PerformanceItem) -> tuple[list[str] | None, int | None]:
    if item.metric_type == "benchmark_return" or (item.subject or "").replace(" ", "") == "비교지수":
        return _find_subject_row(table, "비교지수")
    if item.metric_type == "volatility" or "변동성" in compact(item.subject):
        return _find_subject_row(table, "변동성")
    row, index = _find_class_row(table, item.class_name or item.subject)
    if row is not None:
        return row, index
    if item.class_name:
        target = class_identity(item.class_name)
        col = PERIOD_COLUMNS.get(item.period or "")
        prefix_matches = []
        if target and col is not None:
            for candidate_index, candidate in enumerate(table.rows):
                if not candidate or col >= len(candidate):
                    continue
                prefix = re.match(r"^([A-Za-z][A-Za-z0-9\-]*)\(", compact(candidate[0]))
                if (
                    prefix
                    and prefix.group(1).lower() == target
                    and approx_equal(parse_number(candidate[col]), item.return_rate)
                ):
                    prefix_matches.append((candidate, candidate_index))
        if len(prefix_matches) == 1:
            return prefix_matches[0]
    # Summary performance tables often label the fund row as "펀드Class A"
    # while canonical extraction uses the generic subject "투자신탁". Accept
    # the row only when the expected period/value identifies one unique
    # non-benchmark, non-volatility row.
    if not item.class_name and compact(item.subject) in {"투자신탁", "펀드"}:
        col = PERIOD_COLUMNS.get(item.period or "")
        matches = []
        if col is not None:
            for candidate_index, candidate in enumerate(table.rows):
                label = compact(candidate[0]) if candidate else ""
                if (
                    col < len(candidate)
                    and "비교지수" not in label
                    and "변동성" not in label
                    and approx_equal(parse_number(candidate[col]), item.return_rate)
                ):
                    matches.append((candidate, candidate_index))
        if len(matches) == 1:
            return matches[0]
    return None, None


def _find_subject_row(table: DetectedTable, token: str) -> tuple[list[str] | None, int | None]:
    needle = compact(token)
    for index, row in enumerate(table.rows):
        if row and needle in compact(row[0]):
            return row, index
    return None, None


def _verify_unlabeled_performance(base: dict, table: DetectedTable, item: PerformanceItem) -> VerificationItem:
    label = item.class_name or item.subject
    if not _row_labels_lost(table):
        return VerificationItem(
            status="FAIL",
            verdict="UNSUPPORTED",
            reason=f"표에서 성과 행을 찾지 못했습니다: {label}",
            **base,
        )
    col = PERIOD_COLUMNS.get(item.period or "")
    if col is None:
        return VerificationItem(
            status="SKIPPED",
            verdict="UNVERIFIABLE",
            reason="성과 표의 행 레이블이 손실되어 열 매핑을 할 수 없습니다.",
            **base,
        )
    matches: list[tuple[list[str], int]] = []
    other_hits: list[tuple[int, int]] = []
    for index, row in enumerate(table.rows):
        if not row:
            continue
        if col < len(row) and approx_equal(parse_number(row[col]), item.return_rate):
            matches.append((row, index))
        for other_index, cell in enumerate(row):
            if other_index == col:
                continue
            if approx_equal(parse_number(cell), item.return_rate):
                other_hits.append((index, other_index))
    if len(matches) == 1:
        row, row_index = matches[0]
        return _compare_numeric_cell(
            base,
            expected=item.return_rate,
            cell=row[col],
            row=row,
            col=col,
            location=f"unlabeled row={row_index} column={col} ({item.period})",
            blank_ok=False,
            blank_tokens=(),
        )
    if len(matches) > 1:
        return VerificationItem(
            status="SKIPPED",
            verdict="UNVERIFIABLE",
            reason="성과 표의 행 레이블이 손실되어 행 정체성을 확인할 수 없습니다.",
            **base,
        )
    if other_hits:
        return VerificationItem(
            status="FAIL",
            verdict="CONTRADICTED",
            reason=f"추출값 {item.return_rate}이 {item.period} 열이 아닌 다른 셀 {other_hits}에 있습니다.",
            **base,
        )
    return VerificationItem(
        status="FAIL",
        verdict="CONTRADICTED",
        reason=f"성과 표에서 추출값 {item.return_rate}을 찾지 못했습니다.",
        **base,
    )


def _row_labels_lost(table: DetectedTable) -> bool:
    data_rows = [row for row in table.rows if row]
    if not data_rows:
        return False
    header = compact(" ".join(table.headers))
    expects_label = any(token in header for token in ("종류", "클래스", "구분"))
    unlabeled = 0
    for row in data_rows:
        first = (row[0] or "").strip()
        if looks_like_date(first) or (parse_number(first) is not None and not any(ch.isalpha() or "가" <= ch <= "힣" for ch in first)):
            unlabeled += 1
    return expects_label and unlabeled >= max(1, (len(data_rows) + 1) // 2)


def _verify_fee_from_text(
    base: dict,
    item: FeeItem,
    refs: list[str],
    chunks: list[Chunk],
) -> VerificationItem:
    blob = compact(_fee_source_text(refs, chunks))
    base = {**base, "method": "text_fallback"}
    if not blob:
        return VerificationItem(
            status="SKIPPED",
            verdict="UNVERIFIABLE",
            reason="연결된 표를 찾지 못했고 evidence 원문도 비어 있습니다.",
            **base,
        )
    fee_type = item.fee_type or ""
    # Single-share 투자비용 summary: 투자신탁 / 없음 / total / sales_rem / peer / total_exp
    summary = _parse_trust_fee_summary(blob)
    window = _fee_class_window(blob, item.class_name)
    parsed = summary or (_parse_fee_block(window) if window else {})
    if parsed:
        mapped = parsed.get(fee_type)
        if item.rate is None:
            if mapped is None:
                return VerificationItem(
                    status="PASS",
                    verdict="SUPPORTED",
                    reason=f"클래스 문맥에서 {fee_type}이 없음/공란으로 확인됩니다.",
                    **base,
                )
            return VerificationItem(
                status="FAIL",
                verdict="CONTRADICTED",
                reason=f"클래스 문맥의 {fee_type} 값이 {mapped}인데 추출값은 비어 있습니다.",
                **base,
            )
        if mapped is not None and approx_equal(mapped, item.rate):
            return VerificationItem(
                status="PASS",
                verdict="SUPPORTED",
                reason=f"클래스 문맥에서 {item.class_name}/{fee_type}={item.rate}가 확인됩니다.",
                **base,
            )
        other_keys = [key for key, value in parsed.items() if key != fee_type and approx_equal(value, item.rate)]
        if other_keys:
            return VerificationItem(
                status="FAIL",
                verdict="CONTRADICTED",
                reason=f"클래스 문맥에서 {item.rate}는 {other_keys}에 해당하고 {fee_type}은 {mapped}입니다.",
                **base,
            )
        if mapped is not None:
            return VerificationItem(
                status="FAIL",
                verdict="CONTRADICTED",
                reason=f"클래스 문맥의 {fee_type} 값은 {mapped}인데 추출값은 {item.rate}입니다.",
                **base,
            )

    if item.rate is None:
        return VerificationItem(
            status="SKIPPED",
            verdict="UNVERIFIABLE",
            reason="구조화된 표가 없고 수수료 공란을 특정할 수 없습니다.",
            **base,
        )
    if window and _rate_in_text(window, item.rate):
        if _fee_label_near_rate(window, fee_type, item.rate):
            return VerificationItem(
                status="PASS",
                verdict="SUPPORTED",
                reason=f"클래스 문맥에서 {fee_type} 라벨 근처의 {item.rate}가 확인됩니다.",
                **base,
            )
        return VerificationItem(
            status="WARNING",
            verdict="PARTIALLY_SUPPORTED",
            reason="클래스 문맥에 숫자는 있으나 fee_type을 특정할 수 없습니다.",
            **base,
        )
    if window and _rate_in_text(blob, item.rate):
        return VerificationItem(
            status="FAIL",
            verdict="CONTRADICTED",
            reason="해당 클래스 문맥에 값이 없고 다른 구간에만 존재합니다.",
            **base,
        )
    if _rate_in_text(blob, item.rate):
        return VerificationItem(
            status="SKIPPED",
            verdict="UNVERIFIABLE",
            reason="숫자는 evidence에 있으나 해당 클래스/항목 문맥에 묶이지 않습니다.",
            **base,
        )
    return VerificationItem(
        status="FAIL",
        verdict="UNSUPPORTED",
        reason="evidence 원문에서 해당 클래스의 수수료 값을 확인하지 못했습니다.",
        **base,
    )


def _parse_trust_fee_summary(blob: str) -> dict[str, float | None]:
    """Parse single-share 투자비용 rows labeled 투자신탁 (compact text)."""
    match = re.search(
        r"투자신탁(?:없음|-)?(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)",
        blob,
    )
    if not match:
        return {}
    rates = [float(match.group(i)) for i in range(1, 5)]
    if any(rate >= 10 for rate in rates):
        return {}
    return {
        "sales_fee": None,
        "total_fee": rates[0],
        "sales_remuneration": rates[1],
        "peer_group_total_fee": rates[2],
        "total_fee_and_expenses": rates[3],
    }


def _fee_source_text(refs: list[str], chunks: list[Chunk]) -> str:
    chunk_map = {chunk.chunk_id: chunk.text for chunk in chunks}
    return "\n".join(chunk_map[ref] for ref in refs if chunk_map.get(ref))


def _class_stem(name: str | None) -> str:
    text = compact(name)
    return re.sub(r"\([A-Za-z][A-Za-z0-9\-]*\)$", "", text)


def _fee_class_window(blob: str, class_name: str | None) -> str | None:
    stem = _class_stem(class_name)
    target = class_identity(class_name)
    if not stem and not target:
        return None
    spans: list[tuple[int, int, str]] = []
    for match in FEE_TEXT_CLASS_RE.finditer(blob):
        spans.append((match.start(), match.end(), match.group(0)))
    if not spans:
        start = blob.find(stem) if stem else -1
        if start < 0 and target:
            # Fall back to bare class code token when labels are split.
            code_match = re.search(
                rf"(?<![A-Za-z0-9]){re.escape(target)}(?![A-Za-z0-9])",
                blob,
                flags=re.I,
            )
            start = code_match.start() if code_match else -1
        if start < 0:
            return None
        rest = blob[start:]
        nxt = re.search(r"수수료(?:선취|후취|미징구)|(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9\-]*\(수수료", rest[max(len(stem), 1):])
        end = start + (nxt.start() + max(len(stem), 1) if nxt else min(len(rest), 220))
        return re.sub(r"\|+", " ", blob[start:end])
    for index, (start, end, token) in enumerate(spans):
        if class_identity(token) != target and _class_stem(token) != stem:
            continue
        stop = spans[index + 1][0] if index + 1 < len(spans) else min(len(blob), end + 220)
        note = blob.find("주1)", end)
        if note >= 0:
            stop = min(stop, note)
        window = re.sub(r"\|+", " ", blob[start:stop])
        return window
    return None


def _parse_fee_block(window: str) -> dict[str, float | None]:
    if not window:
        return {}
    class_match = FEE_TEXT_CLASS_RE.search(window[:160])
    rest = window[class_match.end() :] if class_match else window
    rest = re.sub(r"\|+", " ", rest)
    sales_match = re.search(
        r"납입금액의(\d+(?:\.\d+)?)%이내|환매금액의(\d+(?:\.\d+)?)%이내|없음",
        rest,
    )
    if not sales_match:
        # Some rows put only numeric rates after the class token.
        percents = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)%", rest)]
        if len(percents) < 3:
            return {}
        parsed = {key: None for key in FEE_TEXT_SEQUENCE}
        for fee_type, value in zip(FEE_TEXT_SEQUENCE[1:], percents):
            if value is not None and value < 10:
                parsed[fee_type] = value
        return parsed
    parsed: dict[str, float | None] = {key: None for key in FEE_TEXT_SEQUENCE}
    if sales_match.group(1):
        parsed["sales_fee"] = float(sales_match.group(1))
    elif sales_match.group(2):
        parsed["sales_fee"] = float(sales_match.group(2))
    percents = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)%", rest[sales_match.end() :])]
    for fee_type, value in zip(FEE_TEXT_SEQUENCE[1:], percents):
        # Ignore KRW-thousand cost examples that follow the rate columns.
        if value >= 10 and fee_type == "total_fee_and_expenses":
            break
        parsed[fee_type] = value
    return parsed


def _rate_in_text(text: str, rate: float) -> bool:
    for match in re.finditer(r"\d+(?:\.\d+)?", text.replace(",", "")):
        try:
            if approx_equal(float(match.group(0)), rate):
                return True
        except ValueError:
            continue
    return False


def _fee_label_near_rate(window: str, fee_type: str, rate: float) -> bool:
    labels = FEE_TEXT_LABELS.get(fee_type) or ()
    for match in re.finditer(r"\d+(?:\.\d+)?", window.replace(",", "")):
        try:
            value = float(match.group(0))
        except ValueError:
            continue
        if not approx_equal(value, rate):
            continue
        start = max(0, match.start() - 24)
        nearby = window[start : match.end() + 8]
        if any(compact(label) in nearby for label in labels):
            return True
        if fee_type == "sales_fee" and "납입금액의" in window and "이내" in nearby:
            return True
    return False
