from __future__ import annotations

import re
from datetime import datetime

NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
DATE_TOKEN_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def compact(text: str | None) -> str:
    return re.sub(r"[\s\u3000]+", "", text or "").replace("ㆍ", "·")


def parse_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = NUMBER_RE.search(str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def numbers_in(text: str | None) -> list[float]:
    found: list[float] = []
    for match in NUMBER_RE.finditer((text or "").replace(",", "")):
        try:
            found.append(float(match.group(0)))
        except ValueError:
            continue
    return found


def approx_equal(left: float | None, right: float | None, tol: float = 0.0005) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= tol


def format_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text
    return str(value)


def date_variants(value: str | None) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    variants = [compact(text)]
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        year, month, day = match.groups()
        variants.extend(
            [
                f"{year}년{int(month)}월{int(day)}일",
                f"{year}년{int(month):02d}월{int(day):02d}일",
                f"{year}.{int(month)}.{int(day)}",
                f"{year}.{int(month):02d}.{int(day):02d}",
                f"{year}/{int(month)}/{int(day)}",
                f"{year}/{int(month):02d}/{int(day):02d}",
                f"{year}-{int(month)}-{int(day)}",
                f"{year}-{int(month):02d}-{int(day):02d}",
            ]
        )
        return list(dict.fromkeys(variants))
    parsed = DATE_TOKEN_RE.search(text)
    if parsed:
        year, month, day = parsed.groups()
        iso = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        return date_variants(iso)
    return variants


def contains_text(haystack: str | None, needle: str | None) -> bool:
    left = compact(haystack)
    right = compact(needle)
    return bool(right) and right in left


def contains_any_date(haystack: str | None, value: str | None) -> bool:
    blob = compact(haystack)
    return any(token in blob for token in date_variants(value) if token)


def looks_like_date(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if DATE_TOKEN_RE.search(raw):
        return True
    try:
        datetime.strptime(raw[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def token_overlap(extracted: str | None, evidence: str | None) -> float:
    left = compact(extracted)
    right = compact(evidence)
    if not left or not right:
        return 0.0
    if left in right:
        return 1.0
    window = max(6, min(12, len(left) // 4 or 6))
    hits = 0
    total = 0
    for index in range(0, len(left) - window + 1, window):
        total += 1
        if left[index : index + window] in right:
            hits += 1
    return hits / total if total else 0.0
