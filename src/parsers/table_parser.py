from __future__ import annotations

import re

from schemas.chunk import SectionType
from schemas.document import DetectedTable

NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
SHORT_DATE_RE = re.compile(r"^\d{2}[-/.]\d{1,2}[-/.]\d{1,2}$")
CLASS_CONT_RE = re.compile(
    r"^[-–]?(오프라인|온라인|직판|퇴직연금|퇴직|연금|슈퍼)?.{0,24}\([A-Za-z0-9\-]+\)$"
    r"|^[-–]?\([A-Za-z0-9\-]+\)$"
    r"|^(오프라인|온라인)(\([A-Za-z0-9\-]+\))?$"
)
HEADER_JUNK = {
    "투자비용",
    "클래스종류",
    "클래스 종류",
    "종류",
    "구분",
    "투자자가부담하는수수료",
}
FEE_HEADERS = ["클래스 종류", "판매수수료", "총보수", "판매보수", "동종유형 총보수", "총보수·비용"]
PERF_HEADERS = ["종류", "최초설정일", "최근 1년", "최근 2년", "최근 3년", "최근 5년", "설정일이후"]
SALES_TOKENS = {"-", "없음", "해당없음", "해당사항없음", "해당사항 없음"}
PERF_SKIP_NAMES = (
    "연평균수익률",
    "단위:%",
    "단위%)",
    "(연평균",
    "최초설정일",
)
RISK_NAME_COLUMN = "RISK_NAME_COLUMN"
RISK_DESCRIPTION_COLUMN = "RISK_DESCRIPTION_COLUMN"


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _nonempty(row: list[object]) -> list[str]:
    return [_clean_cell(cell) for cell in row if _clean_cell(cell)]


def _is_number(text: str) -> bool:
    return bool(NUMBER_RE.match(text.replace(",", "")))


def _number_value(text: str) -> float | None:
    if not _is_number(text):
        return None
    return float(text.replace(",", ""))


def _is_cost_example(text: str) -> bool:
    value = _number_value(text)
    return value is not None and value >= 10


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def normalize_risk_header_role(text: str | None) -> str | None:
    compact = re.sub(r"[^가-힣A-Za-z0-9]", "", text or "")
    if compact in {"구분", "세부구분", "위험구분", "투자위험구분"}:
        return RISK_NAME_COLUMN
    if "투자위험" in compact and "주요내용" in compact:
        return RISK_DESCRIPTION_COLUMN
    if compact in {"주요투자위험내용", "위험의주요내용"}:
        return RISK_DESCRIPTION_COLUMN
    return None


def risk_column_roles(headers: list[str]) -> list[str]:
    return [normalize_risk_header_role(header) or "" for header in headers]


def is_semantic_risk_table(table: DetectedTable) -> bool:
    roles = table.column_roles or risk_column_roles(table.headers)
    if RISK_NAME_COLUMN in roles and RISK_DESCRIPTION_COLUMN in roles:
        return True
    probe = table.headers + [cell for row in table.rows[:3] for cell in row]
    inferred = {normalize_risk_header_role(cell) for cell in probe}
    return RISK_NAME_COLUMN in inferred and RISK_DESCRIPTION_COLUMN in inferred


def _looks_like_class(text: str) -> bool:
    compact = _compact(text)
    if not compact or compact in HEADER_JUNK or compact.startswith("투자자"):
        return False
    if len(compact) > 80:
        return False
    if "수수료" in compact and re.search(r"\([A-Za-z][A-Za-z0-9\-]*\)", compact):
        return True
    if re.match(r"종류[A-Za-z][A-Za-z0-9\-]*", compact):
        return True
    if re.search(r"\([A-Za-z][A-Za-z0-9\-]*\)$", compact) and any(
        token in compact for token in ("수수료", "오프라인", "온라인", "퇴직", "종류")
    ):
        return True
    return "수수료" in compact and any(token in compact for token in ("선취", "미징구", "후취"))


def _is_class_continuation(text: str) -> bool:
    compact = _compact(text).replace("–", "-")
    if not compact or _is_number(compact):
        return False
    if CLASS_CONT_RE.match(compact):
        return True
    return bool(
        re.match(
            r"^[-–]?(오프라인|온라인슈퍼|온라인|직판|퇴직연금|개인연금|퇴직|연금|슈퍼)$",
            compact,
        )
    )


def _join_class_parts(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if left.endswith("-") or right.startswith("-"):
        return f"{left.rstrip('-')}{right if right.startswith('-') else '-' + right}"
    if right.startswith("("):
        return f"{left}{right}"
    return f"{left}{right}"


def _merge_wrapped_rows(rows: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    for row in rows:
        cells = _nonempty(row)
        if not cells:
            continue
        if merged and len(cells) == 1 and _is_class_continuation(cells[0]):
            merged[-1][0] = _join_class_parts(merged[-1][0], cells[0])
            continue
        if merged and len(cells) == 1 and merged[-1] and merged[-1][0].endswith("-"):
            merged[-1][0] = _join_class_parts(merged[-1][0], cells[0])
            continue
        if (
            merged
            and "(" not in merged[-1][0]
            and "수수료" in merged[-1][0]
            and _is_class_continuation(cells[0])
        ):
            merged[-1][0] = _join_class_parts(merged[-1][0], cells[0])
            if len(cells) > 1:
                merged[-1].extend(cells[1:])
            continue
        merged.append(cells)
    return merged


def _looks_like_fee_header(cells: list[str]) -> bool:
    blob = "".join(cells)
    return "판매수수료" in blob and "총보수" in blob


def _looks_like_performance_header(cells: list[str]) -> bool:
    blob = "".join(cells)
    return "최근1년" in blob.replace(" ", "") or ("최근" in blob and "1년" in blob)


def _has_date(cells: list[str]) -> bool:
    return any(_is_date(c) for c in cells)


def _is_date(text: str) -> bool:
    normalized = text.replace(".", "-").replace("/", "-")
    return bool(DATE_RE.match(normalized) or SHORT_DATE_RE.match(normalized))


def _normalize_date(text: str) -> str:
    normalized = text.replace(".", "-").replace("/", "-")
    if SHORT_DATE_RE.match(normalized):
        return f"20{normalized}"
    return normalized


def _is_sales_token(text: str) -> bool:
    compact = _compact(text)
    return compact in {_compact(item) for item in SALES_TOKENS} or "납입" in text


def _recover_class_name(values: list[str]) -> str | None:
    blob = _compact("".join(values))
    match = re.search(
        r"수수료(선취|후취|미징구)(?:-?(오프라인|온라인슈퍼|온라인|직판))?.*?-?(퇴직연금|개인연금)?.*?\(([A-Za-z][A-Za-z0-9\-]*)\)",
        blob,
    )
    if match:
        pieces = [f"수수료{match.group(1)}"]
        if match.group(2):
            pieces.append(match.group(2))
        if match.group(3):
            pieces.append(match.group(3))
        return f"{'-'.join(pieces)}({match.group(4)})"
    kind = re.search(r"종류([A-Za-z][A-Za-z0-9\-]*)", blob)
    if kind:
        return f"종류{kind.group(1)}"
    return None


def _parse_fee_cells(cells: list[str]) -> list[str] | None:
    values = [cell for cell in cells if cell]
    if not values:
        return None
    if len(values[0]) > 80:
        values = values[1:] or values
    if values and (_compact(values[0]) in {_compact(item) for item in HEADER_JUNK} or values[0] == "투자비용"):
        values = values[1:]
    if not values:
        return None
    joined = " ".join(values)
    if _has_date(values) or "비교지수" in joined or "변동성" in joined:
        return None

    class_name = _recover_class_name(values) or values[0]
    sales = ""
    nums: list[str] = []
    for cell in values[1:]:
        # 일부 PDF는 총보수·비용의 소수점(.)을 천단위 구분자처럼 쉼표(,)로
        # 추출한다 (예: 1.807% -> "1,807"). 수수료 비율 4번째 숫자 자리에서는
        # 이를 투자비용 예시 금액으로 오인하지 않고 소수점으로 복원한다.
        if len(nums) == 3 and re.fullmatch(r"\d,[0-9]{3}", cell.strip()):
            nums.append(cell.strip().replace(",", "."))
            continue
        if _is_cost_example(cell):
            break
        if _is_number(cell):
            nums.append(cell.replace(",", ""))
            continue
        if _looks_like_class(cell) and not _looks_like_class(class_name):
            class_name = cell
            continue
        if _is_sales_token(cell) and not sales:
            sales = cell
            continue
        if not sales and not _looks_like_class(cell):
            sales = cell
    if len(nums) < 3:
        return None
    if not (_looks_like_class(class_name) or "수수료" in class_name or class_name.startswith("종류") or _compact(class_name) == "투자신탁"):
        return None
    if len(nums) >= 4:
        mapped = [class_name, sales, nums[0], nums[1], nums[2], nums[3]]
    else:
        mapped = [class_name, sales, nums[0], nums[1], "", nums[2]]
    return mapped


def _fee_data_row(cells: list[str]) -> bool:
    return _parse_fee_cells(cells) is not None


def _skip_performance_name(name: str) -> bool:
    compact = _compact(name)
    if any(token in compact for token in PERF_SKIP_NAMES):
        return True
    return compact in {"종류", "투자비용", "투자실적", "투자실적추이"}


def _performance_data_row(cells: list[str]) -> bool:
    numbers = [c for c in cells if _is_number(c)]
    if not numbers:
        return False
    joined = " ".join(cells)
    if "납입금액" in joined or "이내" in joined:
        return False
    name = cells[0] if cells else ""
    # Date-shifted broken rows must not be treated as performance subjects.
    if _is_date(name):
        return False
    # Fee summary rows often use '-' / 없음 sales tokens in the second column.
    if len(cells) >= 2 and _is_sales_token(cells[1]):
        return False
    if any(token in joined for token in ("비교지수", "변동성")):
        return True
    if _has_date(cells):
        return True
    if _looks_like_class(name) or name.startswith("종류") or _compact(name) == "투자신탁":
        return True
    if len(cells) >= 2 and _looks_like_class(cells[1]):
        return True
    if _compact(name) in {"펀드", "투자신탁"} or name in {"펀드", "비교지수", "수익률 변동성"}:
        return len(numbers) >= 1
    return False


def reconstruct_fee_table(rows: list[list[str]], table_id: str, page_number: int) -> DetectedTable | None:
    data_rows: list[list[str]] = []
    for cells in rows:
        parsed = _parse_fee_cells(cells)
        if parsed:
            data_rows.append(parsed)
    if not data_rows:
        return None
    return DetectedTable(
        table_id=table_id,
        page_number=page_number,
        section_type=SectionType.FEES,
        headers=list(FEE_HEADERS),
        rows=data_rows,
        raw_row_count=len(rows),
        extraction_method="pymupdf_normalized",
    )


def reconstruct_performance_table(rows: list[list[str]], table_id: str, page_number: int) -> DetectedTable | None:
    data_rows: list[list[str]] = []
    for cells in rows:
        # Keep empty/dash placeholders so sparse rate columns stay aligned.
        cells = [_clean_cell(cell) for cell in cells]
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells) or not _performance_data_row(cells):
            continue
        name = cells[0]
        if name in {"투자실적추이", "(연평균수익률)", "(단위:%)", "투자비용", "종류", "최초설정일"}:
            if len(cells) > 1 and any(token in cells[1] for token in ("비교지수", "변동성")):
                name = cells[1]
                rest = cells[2:]
            else:
                continue
        elif (
            len(cells) >= 2
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]*", _compact(name) or "")
            and (_looks_like_class(cells[1]) or "수수료" in cells[1])
        ):
            # Cover/body tables often put class code then fee-class label.
            recovered = _recover_class_name(cells[1:]) or cells[1]
            name = recovered
            rest = cells[2:]
        else:
            rest = cells[1:]
        if name.startswith("투자실적") and "비교지수" in "".join(cells):
            name = "비교지수"
            rest = [c for c in cells if c != name and "투자실적" not in c and "연평균" not in c]
        if _skip_performance_name(name) and "비교지수" not in name and "변동성" not in name:
            continue
        recovered = _recover_class_name([name, *[cell for cell in rest if cell]])
        if recovered:
            name = recovered
        inception = ""
        rate_cells: list[str] = []
        for cell in rest:
            if not inception and _is_date(cell):
                inception = _normalize_date(cell)
                continue
            if _is_number(cell):
                rate_cells.append(cell.replace(",", ""))
                continue
            if cell in {"-", "–", "—", ""}:
                # Leading blank is the optional inception placeholder on already
                # normalized rows; do not treat it as a rate slot.
                if not rate_cells and not inception and cell == "":
                    continue
                rate_cells.append("")
                continue
        if not any(rate_cells):
            continue
        data_rows.append([name, inception, *rate_cells[:5]])
    if not data_rows:
        return None
    normalized = []
    for row in data_rows:
        padded = row + [""] * (len(PERF_HEADERS) - len(row))
        normalized.append(padded[: len(PERF_HEADERS)])
    return DetectedTable(
        table_id=table_id,
        page_number=page_number,
        section_type=SectionType.PERFORMANCE,
        headers=list(PERF_HEADERS),
        rows=normalized,
        raw_row_count=len(rows),
        extraction_method="pymupdf_normalized",
    )


def reconstruct_risk_table(
    rows: list[list[object]], table_id: str, page_number: int
) -> DetectedTable | None:
    cleaned = [[_clean_cell(cell) for cell in row] for row in rows if row]
    header_index = -1
    headers: list[str] = []
    roles: list[str] = []
    for index, row in enumerate(cleaned):
        candidate_roles = risk_column_roles(row)
        if RISK_NAME_COLUMN in candidate_roles and RISK_DESCRIPTION_COLUMN in candidate_roles:
            header_index = index
            headers = row
            roles = candidate_roles
            break
    if header_index < 0:
        return None

    name_index = roles.index(RISK_NAME_COLUMN)
    description_index = roles.index(RISK_DESCRIPTION_COLUMN)
    logical_names: list[str] = []
    logical_descriptions: list[str] = []
    pending_name_parts: list[str] = []

    def name_complete(value: str) -> bool:
        compact = _compact(value).rstrip(".,;:：")
        return bool("위험" in compact and not re.search(r"(?:및|또는|/|·|,)$", compact))

    def flush_name() -> None:
        nonlocal pending_name_parts
        name = re.sub(r"\s+", " ", " ".join(pending_name_parts)).strip()
        if name_complete(name):
            logical_names.append(name)
        pending_name_parts = []

    for row in cleaned[header_index + 1 :]:
        if not any(row):
            continue
        row_roles = risk_column_roles(row)
        if RISK_NAME_COLUMN in row_roles and RISK_DESCRIPTION_COLUMN in row_roles:
            flush_name()
            continue
        name = row[name_index].strip() if name_index < len(row) else ""
        description = row[description_index].strip() if description_index < len(row) else ""
        if name:
            if pending_name_parts and name_complete(" ".join(pending_name_parts)):
                flush_name()
            pending_name_parts.append(name)
            if name_complete(" ".join(pending_name_parts)):
                flush_name()
        if description:
            # Non-empty names mark physical row starts.  Keeping the two column
            # streams independent prevents a wrapped name from shifting every
            # subsequent description by one row.
            if name or not logical_descriptions:
                logical_descriptions.append(description)
            else:
                logical_descriptions[-1] = f"{logical_descriptions[-1]} {description}".strip()
        elif not name:
            continuation = " ".join(cell for cell in row if cell).strip()
            if continuation and logical_descriptions:
                logical_descriptions[-1] = f"{logical_descriptions[-1]} {continuation}".strip()
    flush_name()
    restored = [
        [name, description]
        for name, description in zip(logical_names, logical_descriptions)
        if name and description
    ]
    if not restored:
        return None
    return DetectedTable(
        table_id=table_id,
        page_number=page_number,
        section_type=SectionType.INVESTMENT_RISK,
        headers=["구분", "투자위험의 주요내용"],
        raw_headers=headers,
        column_roles=[RISK_NAME_COLUMN, RISK_DESCRIPTION_COLUMN],
        rows=restored,
        raw_row_count=len(rows),
        extraction_method="pymupdf_normalized",
    )


def normalize_page_tables(raw_tables: list[list[list[object]]], document_id: str, page_number: int) -> list[DetectedTable]:
    detected: list[DetectedTable] = []
    seq = 1
    for raw in raw_tables:
        risk = reconstruct_risk_table(
            raw, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number
        )
        if risk:
            detected.append(risk)
            seq += 1
            continue
        merged = _merge_wrapped_rows([[_clean_cell(c) for c in row] for row in raw])
        if not merged:
            continue
        fee = reconstruct_fee_table(merged, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number)
        if fee:
            detected.append(fee)
            seq += 1
        perf = reconstruct_performance_table(merged, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number)
        if perf:
            detected.append(perf)
            seq += 1
        if fee or perf:
            continue
        headers = merged[0]
        rows = merged[1:]
        blob = " ".join(headers)
        section = SectionType.OTHER
        if _looks_like_fee_header(headers) or "총보수" in blob:
            section = SectionType.FEES
        elif _looks_like_performance_header(headers):
            section = SectionType.PERFORMANCE
        detected.append(
            DetectedTable(
                table_id=f"{document_id}_p{page_number:03d}_t{seq:03d}",
                page_number=page_number,
                section_type=section,
                headers=headers,
                rows=rows,
                raw_row_count=len(merged),
                extraction_method="pymupdf",
            )
        )
        seq += 1
    return detected


def fallback_tables_from_text(
    text: str,
    document_id: str,
    page_number: int,
    seq_start: int = 90,
) -> list[DetectedTable]:
    """Heading + numeric-row fallback when PyMuPDF table geometry is unreliable."""
    detected: list[DetectedTable] = []
    compact = re.sub(r"\s+", " ", text)
    seq = seq_start

    fee_rows: list[list[str]] = []
    fee_pattern = re.compile(
        r"(수수료[^\n]{0,48}\([A-Za-z0-9\-]+\))\s+"
        r"(납입금액의\s*[\d.]+%\s*이내|-|없음|해당없음)?\s*"
        r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(?:-|해당없음|(-?\d+\.\d+))\s+(-?\d+\.\d+)"
    )
    if "투자비용" in compact or ("판매수수료" in compact and "총보수" in compact):
        for match in fee_pattern.finditer(compact):
            peer = match.group(5) or ""
            expense = match.group(6)
            fee_rows.append(
                [
                    re.sub(r"\s+", "", match.group(1)),
                    (match.group(2) or "").strip(),
                    match.group(3),
                    match.group(4),
                    peer,
                    expense,
                ]
            )
        prefix_fee_pattern = re.compile(
            r"([A-Za-z][A-Za-z0-9\-]*)\s*\(\s*"
            r"(수수료(?:선취|후취|미징구)[^)]{0,80})\)\s*"
            r"(납입금액의\s*[\d.]+\s*%\s*이내|-|없음)\s*"
            r"([\d.]+)\s*%\s+([\d.]+)\s*%\s+([\d.]+)\s*%\s+([\d.]+)\s*%"
        )
        for match in prefix_fee_pattern.finditer(compact):
            description = re.sub(r"\s+", "", match.group(2)).replace("–", "-")
            fee_rows.append([
                f"{description}({match.group(1)})",
                re.sub(r"\s+", " ", match.group(3)).strip(),
                match.group(4),
                match.group(5),
                match.group(6),
                match.group(7),
            ])
        aggregate_fee_pattern = re.compile(
            r"(투자신탁)\s+(없음|-|해당없음|해당사항없음)?\s*"
            r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(?:-|해당없음|(-?\d+\.\d+))\s+(-?\d+\.\d+)"
        )
        for match in aggregate_fee_pattern.finditer(compact):
            peer = match.group(5) or ""
            fee_rows.append(
                [
                    match.group(1),
                    (match.group(2) or "").strip(),
                    match.group(3),
                    match.group(4),
                    peer,
                    match.group(6),
                ]
            )
    if fee_rows:
        detected.append(
            DetectedTable(
                table_id=f"{document_id}_p{page_number:03d}_t{seq:03d}",
                page_number=page_number,
                section_type=SectionType.FEES,
                headers=list(FEE_HEADERS),
                rows=fee_rows,
                raw_row_count=len(fee_rows),
                extraction_method="text_fallback",
            )
        )
        seq += 1

    perf_rows: list[list[str]] = []
    perf_pattern = re.compile(
        r"(수수료[^\n]{0,48}\([A-Za-z0-9\-]+\)|비교지수|수익률\s*변동성)\s+"
        r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\s+"
        r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"
    )
    search_text = compact
    marker = re.search(r"투자실적추이|연평균수익률", compact)
    if marker:
        search_text = compact[marker.start() : marker.start() + 4000]
    for match in perf_pattern.finditer(search_text):
        perf_rows.append(
            [
                re.sub(r"\s+", " ", match.group(1)).strip(),
                match.group(2).replace("/", "-").replace(".", "-"),
                match.group(3),
                match.group(4),
                match.group(5),
                match.group(6),
                match.group(7),
            ]
        )
    if perf_rows:
        detected.append(
            DetectedTable(
                table_id=f"{document_id}_p{page_number:03d}_t{seq:03d}",
                page_number=page_number,
                section_type=SectionType.PERFORMANCE,
                headers=list(PERF_HEADERS),
                rows=perf_rows,
                raw_row_count=len(perf_rows),
                extraction_method="text_fallback",
            )
        )
    return detected


def tables_to_markdown(table: DetectedTable) -> str:
    headers = table.headers or []
    lines = [
        f"[TABLE_ID: {table.table_id}]",
        f"(page={table.page_number}, section={table.section_type.value})",
    ]
    if headers:
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in table.rows:
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        lines.append("| " + " | ".join(padded[: len(headers) or len(padded)]) + " |")
    return "\n".join(lines)
