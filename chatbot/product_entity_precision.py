"""Conservative diagnostic for generic financial nouns, separate from Gold scoring."""
import re

# Generic fund-family stems and optional asset-class parentheses / particles.
_GENERIC_STEM = (
    r"(?:(?:증권)?(?:자|모)?투자신탁"
    r"|집합투자기구|투자신탁|펀드|클래스|모펀드|자펀드)"
)
_ASSET_CLASS = r"(?:\s*[\(（]\s*(?:주식|채권|혼합|재간접|파생)[^\)）]{0,12}\s*[\)）])?"
_PARTICLES = r"(?:에서|에게|으로|뿐만|까지|부터|로|이|가|은|는|을|를|의|에|도|만|뿐|과|와)*"


def is_generic_financial_noun(candidate: str) -> bool:
    # Full match only: a proprietary prefix can never be swallowed.
    text = (candidate or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(_GENERIC_STEM + _ASSET_CLASS + _PARTICLES, text))


def audit_product_candidates(candidates: list[str]) -> dict:
    return {
        "generic_noun_false_positives": [x for x in candidates if is_generic_financial_noun(x)],
        "remaining_unverified_names": [x for x in candidates if not is_generic_financial_noun(x)],
        "original_scoring_unchanged": True,
    }
