from __future__ import annotations

import re

from processing.class_candidates import (
    class_code,
    class_tokens_from_text,
    is_plausible_class_name,
    normalize_class_name,
    prefer_class_name,
)
from schemas.chunk import Chunk
from schemas.document import DetectedTable


class ClassResolver:
    """Resolve short table labels to one canonical class name."""

    def __init__(
        self,
        chunks: list[Chunk] | None = None,
        tables: list[DetectedTable] | None = None,
    ) -> None:
        self._by_code: dict[str, str] = {}
        for chunk in chunks or []:
            self._collect(chunk.text or "")
            for row in chunk.rows or []:
                self._collect(" ".join(row))
        for table in tables or []:
            self._collect(" ".join(table.headers))
            for row in table.rows:
                self._collect(" ".join(row))

    def _collect(self, text: str) -> None:
        for candidate in class_tokens_from_text(text):
            code = class_code(candidate)
            if not code:
                continue
            key = code.lower()
            current = self._by_code.get(key)
            self._by_code[key] = prefer_class_name(current, candidate) or candidate

    def resolve(self, raw: str | None) -> str | None:
        if re.sub(r"\s+", "", raw or "") == "투자신탁":
            return "투자신탁"
        normalized = normalize_class_name(raw)
        if is_plausible_class_name(normalized):
            code = class_code(normalized)
            if code:
                # A descriptive fee-class label in a table is already an
                # official source span. Do not replace it with a longer label
                # carrying the same code from another page/row.
                if normalized.startswith("수수료"):
                    return normalized
                return self._by_code.get(code.lower(), normalized)
            return normalized
        compact = re.sub(r"[^A-Za-z0-9-]", "", raw or "")
        if not compact:
            return None
        return self._by_code.get(compact.lower())

    @property
    def canonical_names(self) -> list[str]:
        return list(dict.fromkeys(self._by_code.values()))
