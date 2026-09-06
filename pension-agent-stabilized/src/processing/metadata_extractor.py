from __future__ import annotations

import re
from datetime import date

from schemas.chunk import Chunk
from schemas.product import CanonicalProduct, OwnershipOutcome

KOREAN_DATE_RE = r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
PLAIN_DATE_RE = r"(\d{4})[./-]\s*(\d{1,2})[./-]\s*(\d{1,2})"
CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{5,6})(?![A-Za-z0-9])")


def apply_metadata_facts(
    product: CanonicalProduct,
    chunks: list[Chunk] | None,
) -> CanonicalProduct:
    chunks = sorted(chunks or [], key=lambda item: (item.page_start, item.chunk_id))
    as_of = _extract_labeled_date(chunks, ("작성 기준일", "작성기준일"))
    effective = _extract_labeled_date(
        chunks, ("증권신고서 효력발생일", "효력발생일")
    )
    product_name = _extract_labeled_text(
        chunks, ("집합투자기구 명칭", "집합투자기구명칭"), max_length=220
    )
    if not product_name or not _is_plausible_product_name(product_name[0]):
        product_name = _extract_value_before_label(
            chunks, ("집합투자기구 명칭", "집합투자기구명칭")
        )
    if not product_name or not _is_plausible_product_name(product_name[0]):
        product_name = _extract_name_from_cover_code_table(chunks)
    if not product_name or not _is_plausible_product_name(product_name[0]):
        product_name = _extract_name_near_fund_code(chunks)
    if not product_name or not _is_plausible_product_name(product_name[0]):
        product_name = _extract_name_from_prospectus_intro(chunks)
    manager = _extract_labeled_text(
        chunks, ("집합투자업자 명칭", "집합투자업자명칭"), max_length=100
    )
    if not manager or not _is_plausible_manager(manager[0]):
        manager = _extract_manager_after_label(chunks)
    # Never overwrite a usable existing name with a failed/placeholder extract.
    if product_name and _is_plausible_product_name(product_name[0]):
        _set_metadata_text(product, "name", product_name, "집합투자기구 명칭")
    elif _is_plausible_product_name(product.product.name):
        product.product.name = _clean_fund_metadata_value(
            product.product.name or "",
            (
                "집합투자업자명칭",
                "연락처",
                "금융투자협회",
                "펀드코드",
                "투자위험등급",
                "분류",
            ),
        )
    else:
        _set_metadata_text(product, "name", product_name, "집합투자기구 명칭")
    if product.product.name:
        product.product.name = re.sub(
            r"\s+\d{1,2}\.\s*$", "", product.product.name
        ).strip() or None
    _set_metadata_text(product, "manager", manager, "집합투자업자 명칭")
    _set_risk_grade(product, _extract_risk_grade(chunks))
    classification = _extract_classification(chunks)
    if classification:
        product.product.classification = classification[0]
        _replace_outcome(product, OwnershipOutcome(
            field="classification",
            owner="metadata",
            status="VALID",
            reason="Deterministic summary classification match.",
            evidence_refs=[classification[1]],
        ))
    fund_code = _extract_fund_code(chunks, product.product.name)
    _set_metadata_date(product, "as_of_date", as_of, "작성기준일")
    _set_metadata_date(product, "effective_date", effective, "효력발생일")
    _set_fund_code(product, fund_code)
    if product.product.name:
        code = product.product.fund_code or ""
        name = product.product.name
        if code:
            name = re.sub(
                rf"\s*\(?\s*{re.escape(code)}\s*\)?\s*$",
                "",
                name,
                flags=re.I,
            ).strip()
        name = re.sub(r"\s+\d{1,2}\.?\s*$", "", name).strip()
        product.product.name = name or None
    return product


def _extract_labeled_text(
    chunks: list[Chunk], labels: tuple[str, ...], *, max_length: int
) -> tuple[str, str] | None:
    """Read a cover-page label without relying on manager/product dictionaries."""
    stop_labels = (
        "집합투자업자명칭",
        "집합투자업자 명칭",
        "연락처",
        "작성책임자",
        "작성 책임자",
        "모집또는",
        "모집 또는",
        "매출기간",
        "작성기준일",
        "증권신고서",
        "금융투자협회",
        "펀드코드",
        "투자위험등급",
        "투자 위험 등급",
        "분류",
    )
    ranked = sorted(
        chunks,
        key=lambda item: (
            0 if not item.table_id else 1,
            item.page_start,
            item.chunk_id,
        ),
    )
    for chunk in ranked:
        if chunk.page_start > 3:
            continue
        text = chunk.text or ""
        for label in labels:
            single = re.search(
                _flex_label(label) + r"\s*[:：]?\s*(?:\n\s*)?([^\n]{2," + str(max_length) + r"})",
                text,
            )
            # Cover pages sometimes flatten the value onto the same visual
            # block as later metadata; allow a short multi-line capture then
            # cut at the next metadata label.
            bounded = re.search(
                _flex_label(label)
                + r"\s*[:：]?\s*(.{2," + str(max_length) + r"}?)(?=(?:"
                + "|".join(_flex_label(item) for item in stop_labels)
                + r")|$)",
                text,
                flags=re.S,
            )
            match = bounded or single
            if not match:
                continue
            # Prefer the stop-bounded capture when the single-line hit is an
            # incomplete bracketed fund name (common OCR line wrap).
            if (
                bounded
                and single
                and _incomplete_fund_name(single.group(1))
                and not _incomplete_fund_name(bounded.group(1))
            ):
                match = bounded
            elif bounded and single and not _incomplete_fund_name(bounded.group(1)):
                match = bounded
            value = _clean_fund_metadata_value(match.group(1), stop_labels)
            if value and len(value) >= 4 and not _incomplete_fund_name(value):
                return value, chunk.chunk_id
    return None


def _incomplete_fund_name(value: str | None) -> bool:
    text = value or ""
    return text.count("[") != text.count("]") or text.count("(") != text.count(")")


def _clean_fund_metadata_value(raw: str, stop_labels: tuple[str, ...]) -> str:
    value = raw.strip(" \t:：|")
    value = re.sub(r"\s+", " ", value)
    # Stop before another numbered cover-page field if PDF extraction
    # flattened it onto the same line.
    value = re.split(r"\s+\d{1,2}\.\s+(?=[가-힣])", value, maxsplit=1)[0].strip()
    for stop in stop_labels:
        parts = re.split(_flex_label(stop), value, maxsplit=1)
        if len(parts) > 1:
            value = parts[0].strip(" \t:：|")
            break
    # Truncate once a later cover metadata token starts after the fund-name
    # bracket/paren close (do not delete a single character and keep the rest).
    cut = re.search(
        r"(?<=\]|\))\s+(?=집합투|연락처|작성|모집|주소|전화|주식회사|업칭|금융투자|펀드코드|투자위험|분류|\d{1,2}\.)",
        value,
    )
    if cut:
        value = value[: cut.start()].strip(" \t:：|")
    # Numbered cover fields often leak as a trailing "2." after the fund name.
    value = re.split(r"\s+\d{1,2}\.\s*", value, maxsplit=1)[0].strip()
    value = re.split(r"\s+\d{1,2}\.\s+(?=[가-힣])", value, maxsplit=1)[0].strip()
    # Normalize OCR spaces inside asset-type tags.
    value = re.sub(r"\[\s+", "[", value)
    value = re.sub(r"\s+\]", "]", value)
    value = re.sub(r"\(\s+", "(", value)
    value = re.sub(r"\s+\)", ")", value)
    return value.strip(" \t:：|")

def _extract_risk_grade(chunks: list[Chunk]) -> tuple[int, str | None, str] | None:
    fallback: tuple[int, str | None, str] | None = None
    assigned_pattern = re.compile(
        r"(?:([1-6])\s*등급\s*(?:체계\s*)?중\s*)([1-6])\s*등급"
        r"(?:\s*[\[\(]\s*([^\]\)\n]{2,20})\s*[\]\)])?",
    )
    cover_pattern = re.compile(
        r"(?:투자\s*위험\s*등급\s*)?(?:[:：]?\s*)?([1-6])\s*등급\s*"
        r"(?:[\[\(]\s*([^\]\)\n]{2,20})\s*[\]\)])?",
    )
    for chunk in chunks:
        text = chunk.text or ""
        assigned = assigned_pattern.search(text)
        if assigned:
            grade = int(assigned.group(2))
            label = assigned.group(3)
            if not label:
                label_match = re.search(
                    r"해당하는\s*((?:매우\s*)?(?:높은|낮은|보통)\s*위험|다소\s*높은\s*위험)",
                    text[assigned.end() : assigned.end() + 80],
                )
                label = label_match.group(1) if label_match else None
            return grade, (label.strip() if label else None), chunk.chunk_id
        if chunk.page_start > 6:
            continue
        match = cover_pattern.search(text)
        if match:
            found = int(match.group(1)), (match.group(2) or None), chunk.chunk_id
            if found[1]:
                return found
            fallback = fallback or found
    return fallback


def _set_risk_grade(
    product: CanonicalProduct,
    extracted: tuple[int, str | None, str] | None,
) -> None:
    if not extracted:
        return
    grade, label, ref = extracted
    product.product.risk.grade = grade
    if label:
        product.product.risk.label = re.sub(r"\s+", " ", label).strip()
    product.product.risk.evidence_refs = [ref]
    _replace_outcome(product, OwnershipOutcome(
        field="risk_grade",
        owner="metadata",
        status="VALID",
        reason="Deterministic 투자위험등급 match.",
        evidence_refs=[ref],
    ))


def _extract_classification(chunks: list[Chunk]) -> tuple[list[str], str] | None:
    for chunk in chunks:
        if chunk.page_start > 10:
            continue
        match = re.search(r"(?:^|\n)\s*분류\s*\n\s*([^\n]{10,300})", chunk.text or "")
        if not match:
            continue
        values = [item.strip() for item in match.group(1).split(",") if item.strip()]
        if len(values) >= 2 and any("증권(" in item for item in values):
            return values, chunk.chunk_id
    return None


def _extract_labeled_date(
    chunks: list[Chunk],
    labels: tuple[str, ...],
) -> tuple[str, str] | None:
    for chunk in chunks:
        if chunk.page_start > 6:
            continue
        text = chunk.text or ""
        for label in labels:
            pattern = re.compile(
                _flex_label(label)
                + r"\s*[:：]?\s*(?:\n\s*)?"
                + KOREAN_DATE_RE
            )
            match = pattern.search(text)
            if match:
                return _iso_date(match.groups()), chunk.chunk_id
            pattern = re.compile(
                _flex_label(label)
                + r"\s*[:：]?\s*(?:\n\s*)?"
                + PLAIN_DATE_RE
            )
            match = pattern.search(text)
            if match:
                return _iso_date(match.groups()), chunk.chunk_id
    return None


def _extract_fund_code(
    chunks: list[Chunk],
    product_name: str | None,
) -> tuple[str, str, str] | None:
    # Tier 1: an explicit code field on a cover or summary page.
    for chunk in chunks:
        if chunk.page_start > 6:
            continue
        match = re.search(
            _flex_label("펀드코드") + r"\s*[:：]\s*([A-Z0-9]{5,6})",
            chunk.text or "",
            re.I,
        )
        if match and _valid_code(match.group(1)):
            return match.group(1).upper(), chunk.chunk_id, "explicit_code_field"

    # Tier 1b: summary title form "(펀드 코드: 48769)" common in Samsung covers.
    for chunk in chunks:
        if chunk.page_start > 10:
            continue
        match = re.search(
            r"\(\s*펀드\s*코드\s*[:：]\s*([A-Z0-9]{5,6})\s*\)",
            chunk.text or "",
            re.I,
        )
        if match and _valid_code(match.group(1)):
            return match.group(1).upper(), chunk.chunk_id, "parenthetical_code"

    # Tier 2: explicit KOFIA fund-code label.
    for chunk in chunks:
        if chunk.page_start > 15:
            continue
        text = chunk.text or ""
        match = re.search(
            _flex_label("금융투자협회 펀드코드") + r"(?P<body>.{0,300})",
            text,
            re.S,
        )
        if not match:
            continue
        code = _first_valid_code(match.group("body"))
        if code:
            return code, chunk.chunk_id, "explicit_label"

    # Tier 3: summary title line adjacent to the product name.
    compact_name = re.sub(r"\s+", "", product_name or "")
    for chunk in chunks:
        if chunk.page_start > 6:
            continue
        for line in (chunk.text or "").splitlines()[:20]:
            compact_line = re.sub(r"\s+", "", line)
            if compact_name and compact_name not in compact_line:
                continue
            matches = re.findall(r"\(([A-Z0-9]{5,6})\)", compact_line)
            for code in reversed(matches):
                if _valid_code(code):
                    return code, chunk.chunk_id, "adjacent_summary"
    return None


def _is_plausible_product_name(value: str | None) -> bool:
    text = (value or "").strip()
    if len(text) < 6 or re.fullmatch(r"\d+\.?", text):
        return False
    if re.fullmatch(r"명\s*칭", text):
        return False
    # Reject cover boilerplate / sentence fragments, not fund titles.
    if any(
        token in text
        for token in ("투자설명서", "에 대한", "읽어보", "자세한 내용", "금융투자협회")
    ):
        return False
    return any(
        token in text
        for token in ("투자신탁", "집합투자", "펀드", "투자회사")
    )


def _is_plausible_manager(value: str | None) -> bool:
    text = (value or "").strip()
    if len(text) < 4 or re.fullmatch(r"\d+\.?", text):
        return False
    return any(token in text for token in ("운용", "자산", "투자", "㈜", "주식회사"))


def _extract_value_before_label(
    chunks: list[Chunk],
    labels: tuple[str, ...],
) -> tuple[str, str] | None:
    """Cover layouts where the value line precedes '1. 집합투자기구 명칭'."""
    for chunk in chunks:
        if chunk.page_start > 3 or chunk.table_id:
            continue
        text = chunk.text or ""
        for label in labels:
            match = re.search(
                r"([^\n]{6,180})\s*\n\s*\d+\.\s*(?:\n\s*)?"
                + _flex_label(label),
                text,
            )
            if not match:
                continue
            value = _clean_fund_metadata_value(
                match.group(1).lstrip(":：").strip(),
                ("집합투자업자명칭", "연락처", "금융투자협회", "펀드코드"),
            )
            if _is_plausible_product_name(value):
                return value, chunk.chunk_id
    return None


def _extract_name_near_fund_code(chunks: list[Chunk]) -> tuple[str, str] | None:
    """Recover name from summary title '...투자신탁[채권](펀드 코드: 48769)'."""
    pattern = re.compile(
        r"([가-힣A-Za-z0-9\[\]\(\)\- ·]{8,120}?(?:투자신탁|집합투자기구|투자회사)[가-힣A-Za-z0-9\[\]\(\)\- ·]{0,40}?)"
        r"\s*\(\s*펀드\s*코드\s*[:：]\s*[A-Z0-9]{5,6}\s*\)",
        re.I,
    )
    for chunk in chunks:
        if chunk.page_start > 10:
            continue
        match = pattern.search(chunk.text or "")
        if not match:
            continue
        value = _clean_fund_metadata_value(
            match.group(1),
            ("집합투자업자명칭", "연락처", "금융투자협회", "펀드코드"),
        )
        if _is_plausible_product_name(value):
            return value, chunk.chunk_id
    return None


def _extract_name_from_cover_code_table(chunks: list[Chunk]) -> tuple[str, str] | None:
    """Cover blocks that list '명칭 / 금융투자협회 펀드코드' then name+code rows."""
    pattern = re.compile(
        _flex_label("집합투자기구 명칭")
        + r"\s*(?:\n\s*)?(?:명\s*칭\s*)?(?:\n\s*)?(?:"
        + _flex_label("금융투자협회 펀드코드")
        + r"\s*)?(?:\n\s*)*"
        r"([가-힣A-Za-z0-9\[\]\(\)\- ·]{8,120})",
    )
    for chunk in chunks:
        if chunk.page_start > 3:
            continue
        match = pattern.search(chunk.text or "")
        if not match:
            continue
        value = _clean_fund_metadata_value(
            match.group(1),
            ("집합투자업자명칭", "연락처", "금융투자협회", "펀드코드", "판매회사"),
        )
        value = re.sub(
            r"(?:\s+[A-Z0-9]{5,6})?(?:\s+\d{1,2}\.?)?\s*$",
            "",
            value,
            flags=re.I,
        ).strip()
        if _is_plausible_product_name(value):
            return value, chunk.chunk_id
    return None


def _extract_name_from_prospectus_intro(chunks: list[Chunk]) -> tuple[str, str] | None:
    pattern = re.compile(
        r"이\s*투자설명서는\s*([^\n]{8,140}?)\s*에\s*대한",
    )
    for chunk in chunks:
        if chunk.page_start > 3:
            continue
        match = pattern.search(chunk.text or "")
        if not match:
            continue
        value = _clean_fund_metadata_value(
            match.group(1),
            ("집합투자업자명칭", "연락처", "금융투자협회", "펀드코드"),
        )
        if _is_plausible_product_name(value):
            return value, chunk.chunk_id
    return None


def _extract_manager_after_label(chunks: list[Chunk]) -> tuple[str, str] | None:
    for chunk in chunks:
        if chunk.page_start > 3 or chunk.table_id:
            continue
        match = re.search(
            _flex_label("집합투자업자 명칭")
            + r"\s*(?:\n\s*)?[:：]?\s*(?:\n\s*)?([^\n]{4,80})",
            chunk.text or "",
        )
        if not match:
            continue
        value = _clean_fund_metadata_value(
            match.group(1),
            ("판매회사", "작성기준일", "증권신고서", "모집", "연락처"),
        )
        if _is_plausible_manager(value):
            return value, chunk.chunk_id
    return None


def _first_valid_code(text: str) -> str | None:
    for match in CODE_RE.finditer(text):
        code = match.group(1)
        if _valid_code(code):
            return code
    return None


def _valid_code(code: str | None) -> bool:
    value = (code or "").strip().upper()
    return bool(
        value
        and not value.startswith("KR")
        and any(char.isdigit() for char in value)
        and re.fullmatch(r"[A-Z0-9]{5,6}", value)
    )


def _flex_label(label: str) -> str:
    return r"\s*".join(re.escape(char) for char in re.sub(r"\s+", "", label))


def _iso_date(parts: tuple[str, ...]) -> str:
    year, month, day = (int(part) for part in parts[-3:])
    return date(year, month, day).isoformat()


def _replace_outcome(product: CanonicalProduct, outcome: OwnershipOutcome) -> None:
    product.extraction.ownership = [
        item
        for item in product.extraction.ownership
        if not (item.field == outcome.field and item.owner == outcome.owner)
    ]
    product.extraction.ownership.append(outcome)


def _set_metadata_date(
    product: CanonicalProduct,
    field: str,
    extracted: tuple[str, str] | None,
    label: str,
) -> None:
    if extracted:
        value, ref = extracted
        setattr(product.document, field, value)
        outcome = OwnershipOutcome(
            field=field,
            owner="metadata",
            status="VALID",
            reason=f"Deterministic {label} label match.",
            evidence_refs=[ref],
        )
    else:
        outcome = OwnershipOutcome(
            field=field,
            owner="metadata",
            status="NOT_FOUND",
            reason=f"No deterministic {label} label match.",
        )
    _replace_outcome(product, outcome)


def _set_fund_code(
    product: CanonicalProduct,
    extracted: tuple[str, str, str] | None,
) -> None:
    if extracted:
        value, ref, tier = extracted
        product.product.fund_code = value
        name = product.product.name or ""
        product.product.name = re.sub(
            rf"\s*\(\s*{re.escape(value)}\s*\)\s*$", "", name, flags=re.I
        ).strip() or None
        outcome = OwnershipOutcome(
            field="fund_code",
            owner="metadata",
            status="VALID",
            reason=f"Deterministic fund-code match ({tier}).",
            evidence_refs=[ref],
        )
    else:
        if (product.product.fund_code or "").upper().startswith("KR"):
            product.product.fund_code = None
        outcome = OwnershipOutcome(
            field="fund_code",
            owner="metadata",
            status="NOT_FOUND",
            reason="No deterministic fund-code source matched.",
        )
    _replace_outcome(product, outcome)


def _set_metadata_text(
    product: CanonicalProduct,
    field: str,
    extracted: tuple[str, str] | None,
    label: str,
) -> None:
    if extracted:
        value, ref = extracted
        setattr(product.product, field, value)
        outcome = OwnershipOutcome(
            field=field,
            owner="metadata",
            status="VALID",
            reason=f"Deterministic {label} label match.",
            evidence_refs=[ref],
        )
    else:
        outcome = OwnershipOutcome(
            field=field,
            owner="metadata",
            status="NOT_FOUND",
            reason=f"No deterministic {label} label match.",
        )
    _replace_outcome(product, outcome)
