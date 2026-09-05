from __future__ import annotations

import re
from collections import OrderedDict

from schemas.chunk import Chunk
from schemas.product import CanonicalProduct, InvestmentRiskItem

try:  # Optional quality boost; exact/normalized matching remains the primary rule.
    from rapidfuzz.fuzz import ratio as fuzzy_ratio
except Exception:  # pragma: no cover - dependency is optional at runtime
    fuzzy_ratio = None


def normalize_risk_name(name: str | None) -> str:
    """Collapse PDF layout whitespace without changing the risk wording."""
    return re.sub(r"\s+", " ", name or "").strip(" |-·ㆍ,，;；:：")


def is_generic_risk_heading(name: str | None) -> bool:
    compact = re.sub(r"[^가-힣A-Za-z0-9]", "", name or "")
    return compact in RiskHeadingAnchor._STOP


class RiskHeadingAnchor:
    """Anchor risk names to wording that is explicitly present in the source.

    V2 accepts three evidence forms without allowing LLM-created names:
    1) table/bullet/line headings,
    2) explicit section headings,
    3) explicit inline risk-name expressions inside prose (e.g. "금리변동위험, 신용위험").

    The returned name is always source wording. Description-only mechanisms are never
    promoted to a new synthetic risk name.
    """

    _HEADING_RE = re.compile(r"([가-힣A-Za-z0-9·ㆍ()\- /]{2,40}?위험(?:\s*등)?)(?=\s|$|[:：,，;；/])")
    _INLINE_RE = re.compile(r"([가-힣A-Za-z0-9·ㆍ()\- /]{2,34}?위험)(?=[,，;；/\s]|$)")
    _STOP = {
        "투자위험", "주요투자위험", "위험", "위험등급", "높은위험", "낮은위험",
        "보통위험", "매우높은위험", "매우낮은위험", "다소높은위험",
        "집합투자기구의투자위험", "투자위험의주요내용",
    }

    def apply(self, product: CanonicalProduct, chunks: list[Chunk]) -> CanonicalProduct:
        chunk_map = {c.chunk_id: c.text or "" for c in chunks}
        repaired: list[InvestmentRiskItem] = []
        rejected = 0

        for item in product.product.investment_risks:
            evidence_text = "\n".join(chunk_map.get(ref, "") for ref in item.evidence_refs if ref in chunk_map)
            anchored = self._anchor_name(item.name or "", item.description or "", evidence_text)
            if not anchored:
                rejected += 1
                continue
            repaired.append(item.model_copy(update={"name": normalize_risk_name(anchored)}))

        grouped: OrderedDict[str, InvestmentRiskItem] = OrderedDict()
        for item in repaired:
            key = self._compact(item.name)
            prev = grouped.get(key)
            if prev is None:
                grouped[key] = item
                continue
            desc = self._choose_description(prev.description, item.description)
            refs = list(dict.fromkeys([*prev.evidence_refs, *item.evidence_refs]))
            grouped[key] = prev.model_copy(update={"description": desc, "evidence_refs": refs})

        product.product.investment_risks = list(grouped.values())
        if rejected:
            warning = f"RISK_HEADING_ANCHOR_REJECTED: {rejected} synthesized/unanchored risk label(s) removed."
            if warning not in product.extraction.warnings:
                product.extraction.warnings.append(warning)
        return product

    def _anchor_name(self, name: str, description: str, evidence: str) -> str | None:
        compact_name = self._compact(name)
        compact_evidence = self._compact(evidence)
        if compact_name in self._STOP:
            return None
        if compact_name and compact_name in compact_evidence:
            # Return the explicit source spelling when possible rather than the LLM spelling.
            source_match = self._find_equivalent_source_name(name, evidence)
            return normalize_risk_name(source_match or name)

        candidates = self._explicit_names(evidence)
        if not candidates:
            return None

        target_compact = self._compact(name)
        target_tokens = self._tokens(f"{name} {description}")
        scored: list[tuple[float, str]] = []
        for candidate in candidates:
            cand_compact = self._compact(candidate)
            tokens = self._tokens(candidate)
            lexical = len(tokens & target_tokens) / len(tokens) if tokens else 0.0
            char_score = 0.0
            if target_compact and fuzzy_ratio is not None:
                char_score = fuzzy_ratio(target_compact, cand_compact) / 100.0
            elif target_compact:
                common = len(set(target_compact) & set(cand_compact))
                char_score = common / max(1, len(set(cand_compact)))

            score = max(lexical, char_score * 0.9)
            if any(
                term in cand_compact and term in self._compact(name + description)
                for term in (
                    "차입", "신용", "유동성", "환매", "금리", "이자율", "주식", "원본",
                    "원금", "환율", "부도", "종목", "해지", "국가", "추적오차", "파생상품",
                    "집중투자", "레버리지", "대여", "시장",
                )
            ):
                score += 0.25
            scored.append((score, candidate))

        if not scored:
            return None
        score, candidate = max(scored, key=lambda x: x[0])
        return normalize_risk_name(candidate) if score >= 0.58 else None

    def _find_equivalent_source_name(self, name: str, evidence: str) -> str | None:
        target = self._compact(name)
        for candidate in self._explicit_names(evidence):
            if self._compact(candidate) == target:
                return candidate
        # Recover the exact source spelling even when whitespace differs.
        if target:
            pattern = r"\s*".join(re.escape(ch) for ch in target)
            match = re.search(pattern, evidence or "")
            if match:
                return normalize_risk_name(match.group(0))
        return None

    def _explicit_names(self, text: str) -> list[str]:
        out: list[str] = []
        for raw in (text or "").splitlines():
            line = normalize_risk_name(raw)
            if not line:
                continue
            # Tier 1/2: compact heading or bullet lines.
            if len(self._compact(line)) <= 48:
                for match in self._HEADING_RE.finditer(line):
                    self._append_candidate(out, match.group(1))
            # Tier 3: explicit inline risk-name expressions in prose/lists.
            for match in self._INLINE_RE.finditer(line):
                self._append_candidate(out, match.group(1))
        return list(dict.fromkeys(out))

    def _append_candidate(self, out: list[str], raw: str) -> None:
        candidate = normalize_risk_name(raw)
        compact = self._compact(candidate)
        if not candidate or compact in self._STOP:
            return
        # Reject sentence-like spans; a source risk label should be concise.
        if len(compact) > 34:
            return
        if any(marker in compact for marker in ("수익률변동성", "표준편차")):
            return
        out.append(candidate)

    @staticmethod
    def _choose_description(left: str | None, right: str | None) -> str | None:
        if not left:
            return right
        if not right:
            return left
        return left if len(left) >= len(right) else right

    @staticmethod
    def _compact(text: str | None) -> str:
        return re.sub(r"[^가-힣A-Za-z0-9]", "", text or "")

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            x for x in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")
            if x not in {"위험", "손실", "가능성", "발생"}
        }
