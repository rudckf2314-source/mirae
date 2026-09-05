import re


MOJIBAKE_MARKERS = ("�", "?섏", "?먯", "吏묓", "媛", "理쒖", "醫낅")


def looks_mojibake(value: str | None) -> bool:
    """Detect common UTF-8/legacy Korean decoding damage without rejecting punctuation."""
    if not value:
        return False
    marker_hits = sum(value.count(marker) for marker in MOJIBAKE_MARKERS)
    question_marks = value.count("?")
    return marker_hits > 0 or (question_marks >= 2 and question_marks / len(value) >= 0.03)


def normalize_pdf_text(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if not lines:
        return text.strip()
    stripped = [line.strip() for line in lines if line.strip()]
    if not stripped:
        return ""
    avg_len = sum(len(line) for line in stripped) / len(stripped)
    if avg_len < 8:
        collapsed = re.sub(r"\s+", " ", text)
        return collapsed.strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
