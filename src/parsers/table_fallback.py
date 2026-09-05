from __future__ import annotations

import io
import re
from dataclasses import dataclass

from parsers.table_parser import (
    reconstruct_fee_table,
    reconstruct_performance_table,
    reconstruct_risk_table,
)
from parsers.table_quality import NORMAL, assess_table, needed_sections
from schemas.chunk import SectionType
from schemas.document import DetectedTable

LEFT_X_MIN = 90.0
LEFT_X_MAX = 163.0
CLASS_START_RE = re.compile(r"^수수료(?:선취|후취|미징구)")
TOKEN_RE = re.compile(
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|납입금액의"
    r"|-?\d[\d,]*(?:\.\d+)?%이내"
    r"|-?\d[\d,]*(?:\.\d+)?%"
    r"|-?\d[\d,]*(?:\.\d+)?"
    r"|없음|해당없음"
    r"|[^\s]+"
)


@dataclass
class TextBox:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


def recover_page_tables(
    pdf_bytes: bytes,
    page_number: int,
    document_id: str,
    existing: list[DetectedTable],
    page_text: str,
) -> list[DetectedTable]:
    needed = needed_sections(page_text, existing)
    recovered = list(existing)
    if not needed:
        return recovered

    plumber = _pdfplumber_tables(pdf_bytes, page_number, document_id)
    recovered = _replace_better(recovered, plumber, needed, page_number)
    needed = needed_sections(page_text, recovered)
    if not needed:
        return recovered

    miner = _pdfminer_tables(pdf_bytes, page_number, document_id)
    recovered = _replace_better(recovered, miner, needed, page_number)
    needed = needed_sections(page_text, recovered)
    if needed:
        recovered = [
            table
            for table in recovered
            if not (
                table.page_number == page_number
                and table.section_type in needed
                and assess_table(table) != NORMAL
            )
        ]
    return recovered


def _replace_better(
    existing: list[DetectedTable],
    incoming: list[DetectedTable],
    needed: set[SectionType],
    page_number: int,
) -> list[DetectedTable]:
    better = [
        table
        for table in incoming
        if table.section_type in needed and assess_table(table) == NORMAL
    ]
    if not better:
        return existing
    replaced = {table.section_type for table in better}
    kept = [
        table
        for table in existing
        if not (table.page_number == page_number and table.section_type in replaced)
    ]
    return kept + better


def _pdfplumber_tables(pdf_bytes: bytes, page_number: int, document_id: str) -> list[DetectedTable]:
    try:
        import pdfplumber
    except ImportError:
        return []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if page_number > len(pdf.pages):
                return []
            page = pdf.pages[page_number - 1]
            raw_tables = page.extract_tables() or []
    except Exception:
        return []
    detected: list[DetectedTable] = []
    seq = 50
    for raw in raw_tables:
        rows = [[_clean_plumber_cell(cell) for cell in row] for row in raw or []]
        risk = reconstruct_risk_table(
            rows, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number
        )
        if risk:
            risk.extraction_method = "pdfplumber"
            detected.append(risk)
            seq += 1
        fee = reconstruct_fee_table(rows, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number)
        if fee:
            fee.extraction_method = "pdfplumber"
            detected.append(fee)
            seq += 1
        perf = reconstruct_performance_table(rows, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number)
        if perf and assess_table(perf) == NORMAL:
            perf.extraction_method = "pdfplumber"
            detected.append(perf)
            seq += 1
    return detected


def _clean_plumber_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def _pdfminer_tables(pdf_bytes: bytes, page_number: int, document_id: str) -> list[DetectedTable]:
    boxes = _pdfminer_boxes(pdf_bytes, page_number)
    if not boxes:
        return []
    detected: list[DetectedTable] = []
    seq = 70
    fee_rows = _reconstruct_fee_from_boxes(boxes)
    if fee_rows:
        fee = reconstruct_fee_table(fee_rows, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number)
        if fee:
            fee.extraction_method = "pdfminer_coordinate_fallback"
            detected.append(fee)
            seq += 1
    perf_rows = _reconstruct_perf_from_boxes(boxes)
    if perf_rows:
        perf = reconstruct_performance_table(
            perf_rows, f"{document_id}_p{page_number:03d}_t{seq:03d}", page_number
        )
        if perf:
            perf.extraction_method = "pdfminer_coordinate_fallback"
            detected.append(perf)
            seq += 1
    risk_rows = _reconstruct_risk_from_boxes(boxes)
    if risk_rows:
        risk = reconstruct_risk_table(
            [
                ["구분", "투자위험의 주요내용"],
                *risk_rows,
            ],
            f"{document_id}_p{page_number:03d}_t{seq:03d}",
            page_number,
        )
        if risk:
            risk.extraction_method = "pdfminer_risk_coordinate_fallback"
            detected.append(risk)
    return detected


def _pdfminer_boxes(pdf_bytes: bytes, page_number: int) -> list[TextBox]:
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTContainer, LTTextLine
    except ImportError:
        return []

    def iter_lines(node):
        if isinstance(node, LTTextLine):
            yield node
            return
        if isinstance(node, LTContainer):
            for child in node:
                yield from iter_lines(child)

    boxes: list[TextBox] = []
    try:
        pages = extract_pages(io.BytesIO(pdf_bytes), page_numbers=[page_number - 1])
        for page in pages:
            height = float(page.height)
            for line in iter_lines(page):
                text = re.sub(r"\s+", " ", (line.get_text() or "").replace("\n", " ")).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = line.bbox
                boxes.append(
                    TextBox(
                        text=text,
                        x0=float(x0),
                        x1=float(x1),
                        top=height - float(y1),
                        bottom=height - float(y0),
                    )
                )
    except Exception:
        return []
    boxes.sort(key=lambda item: (item.top, item.x0))
    return boxes


def _reconstruct_fee_from_boxes(boxes: list[TextBox]) -> list[list[str]]:
    region = _region_between(boxes, ("판매수수료", "클래스"), ("투자실적", "주1)", "주 1)"))
    if not region:
        return []
    groups = _left_label_groups(region, extra_starts=())
    rows: list[list[str]] = []
    for index, group in enumerate(groups):
        label = _join_label(group)
        if "수수료" not in label:
            continue
        next_top = groups[index + 1][0].top if index + 1 < len(groups) else None
        values = _right_tokens(region, group, next_top)
        rows.append([label, *values])
    return rows


def _reconstruct_perf_from_boxes(boxes: list[TextBox]) -> list[list[str]]:
    region = _region_after(boxes, ("투자실적추이", "투자실적", "연평균수익"))
    if not region:
        return []
    groups = _left_label_groups(region, extra_starts=("비교지수", "수익률변동성", "변동성"))
    rows: list[list[str]] = []
    for index, group in enumerate(groups):
        label = _join_label(group)
        compact = re.sub(r"\s+", "", label)
        if compact in {"종류", "최초설정일"}:
            continue
        if not (
            "수수료" in compact
            or "비교지수" in compact
            or "변동성" in compact
            or compact.startswith("종류")
        ):
            continue
        next_top = groups[index + 1][0].top if index + 1 < len(groups) else None
        values = _right_tokens(region, group, next_top)
        rows.append([label, *values])
    return rows


def _reconstruct_risk_from_boxes(boxes: list[TextBox]) -> list[list[str]]:
    name_header = None
    description_header = None
    for box in boxes:
        blob = re.sub(r"[^가-힣A-Za-z0-9]", "", box.text)
        if name_header is None and blob in {"구분", "세부구분", "위험구분"}:
            name_header = box
        if description_header is None and "투자위험" in blob and "주요내용" in blob:
            description_header = box
        if name_header and description_header:
            break
    if name_header is None or description_header is None:
        return []
    if abs(name_header.top - description_header.top) > 40:
        return []

    split_x = (name_header.x1 + description_header.x0) / 2
    start_top = max(name_header.bottom, description_header.bottom)
    end_top = None
    stop_tokens = ("매입방법", "환매방법", "판매수수료", "투자비용", "기준가격")
    for box in boxes:
        if box.top <= start_top + 5:
            continue
        blob = re.sub(r"\s+", "", box.text)
        if any(blob.startswith(token) for token in stop_tokens):
            end_top = box.top
            break
    region = [
        box for box in boxes
        if box.top >= start_top - 1 and (end_top is None or box.top < end_top)
    ]
    left = [
        box for box in region
        if box.x0 < split_x and len(re.sub(r"\s+", "", box.text)) <= 32
    ]
    left.sort(key=lambda item: (item.top, item.x0))

    groups: list[list[TextBox]] = []
    pending: list[TextBox] = []
    for box in left:
        blob = re.sub(r"\s+", "", box.text)
        if blob in {"구분", "세부구분"}:
            pending = []
            continue
        if pending and box.top - pending[-1].bottom > 12:
            pending = []
        pending.append(box)
        joined = re.sub(r"\s+", "", "".join(item.text for item in pending))
        if "위험" in joined and len(joined) <= 40:
            groups.append(list(pending))
            pending = []
    rows: list[list[str]] = []
    for index, group in enumerate(groups):
        top = min(box.top for box in group) - 2
        next_top = (
            min(box.top for box in groups[index + 1]) - 1
            if index + 1 < len(groups)
            else (end_top or float("inf"))
        )
        description_boxes = [
            box for box in region
            if box.x0 >= split_x and box.bottom >= top and box.top < next_top
        ]
        description_boxes.sort(key=lambda item: (item.top, item.x0))
        name = "".join(box.text.strip() for box in group)
        description = " ".join(box.text.strip() for box in description_boxes)
        description = re.sub(r"\s+", " ", description).strip()
        if name and description:
            rows.append([name, description])
    return rows


def _region_between(boxes: list[TextBox], starts: tuple[str, ...], ends: tuple[str, ...]) -> list[TextBox]:
    start_top = None
    end_top = None
    for box in boxes:
        blob = re.sub(r"\s+", "", box.text)
        if start_top is None and any(token in blob for token in starts) and box.x0 < 250:
            start_top = box.top
        if start_top is not None and any(blob.startswith(token.replace(" ", "")) or token in blob for token in ends):
            if box.top > start_top + 20:
                end_top = box.top
                break
    if start_top is None:
        return []
    return [box for box in boxes if box.top >= start_top - 2 and (end_top is None or box.top < end_top)]


def _region_after(boxes: list[TextBox], starts: tuple[str, ...]) -> list[TextBox]:
    start_top = None
    for box in boxes:
        blob = re.sub(r"\s+", "", box.text)
        if any(token in blob for token in starts) and box.x0 < 200:
            start_top = box.top
            break
    if start_top is None:
        return []
    return [box for box in boxes if box.top >= start_top - 2]


def _left_label_groups(boxes: list[TextBox], extra_starts: tuple[str, ...]) -> list[list[TextBox]]:
    left = [
        box
        for box in boxes
        if LEFT_X_MIN <= box.x0 <= LEFT_X_MAX and not _mostly_numeric(box.text)
    ]
    groups: list[list[TextBox]] = []
    current: list[TextBox] | None = None
    for box in left:
        blob = re.sub(r"\s+", "", box.text)
        if blob in {"클래스종류", "종류", "투자비용", "투자실적추이"}:
            current = None
            continue
        if CLASS_START_RE.match(blob) or any(blob.startswith(token) or token in blob[:8] for token in extra_starts):
            current = [box]
            groups.append(current)
            continue
        if current is None:
            continue
        if box.top - current[-1].bottom > 22:
            current = None
            continue
        current.append(box)
    return groups


def _join_label(group: list[TextBox]) -> str:
    label = re.sub(r"\s+", "", "".join(box.text.strip() for box in group))
    label = re.sub(r"\(%\)$", "", label)
    label = label.replace("(%)", "")
    return re.sub(r"-{2,}", "-", label)


def _right_tokens(
    boxes: list[TextBox],
    group: list[TextBox],
    next_top: float | None = None,
) -> list[str]:
    top = min(box.top for box in group) - 4
    bottom = max(box.bottom for box in group) + 4
    if next_top is not None:
        bottom = min(bottom, next_top - 0.5)
    right = [
        box
        for box in boxes
        if box.x0 > LEFT_X_MAX and box.top <= bottom and box.bottom >= top
    ]
    right.sort(key=lambda item: item.x0)
    tokens: list[str] = []
    for box in right:
        tokens.extend(_explode(box.text))
    merged: list[str] = []
    index = 0
    while index < len(tokens):
        current = tokens[index]
        if current in {"~", "-", "률)", "(%)", "주1)", "주1"}:
            index += 1
            continue
        nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
        if current == "납입금액의" and "이내" in nxt:
            merged.append(f"{current} {nxt}")
            index += 2
            continue
        merged.append(current)
        index += 1
    return merged


def _explode(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(text or ""):
        token = token.strip()
        if not token:
            continue
        if token.endswith("%이내") or token in {"없음", "해당없음", "납입금액의"}:
            tokens.append(token)
            continue
        if token.endswith("%"):
            tokens.append(token[:-1])
            continue
        tokens.append(token)
    return tokens


def _mostly_numeric(text: str) -> bool:
    blob = re.sub(r"\s+", "", text or "")
    if re.match(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$", blob):
        return True
    return bool(re.match(r"^-?\d+(?:\.\d+)?%?$", blob))
