"""Deterministic-first pension-type normalization for derived product search records."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .llm_provider import LLMProvider
from .product_json_schema import PensionTypeCode, PensionTypeNormalization, RawPensionTypeContext
from .product_normalizer_llm import LLMNormalizerSettings, PROMPT_VERSION, ProductNormalizerLLM


NORMALIZATION_POLICY_VERSION = "pension-type-policy-v1"

LABELS_KO = {
    PensionTypeCode.RETIREMENT_PENSION: "퇴직연금",
    PensionTypeCode.PERSONAL_PENSION: "개인연금",
    PensionTypeCode.PENSION_SAVINGS: "연금저축",
    PensionTypeCode.INSTITUTIONAL: "기관",
    PensionTypeCode.EMPLOYEE_WELFARE_PENSION: "근로자복지연금",
    PensionTypeCode.WRAP: "랩",
    PensionTypeCode.OTHER: "기타",
    PensionTypeCode.UNKNOWN: "미확인",
}

_DIRECT_CODES = {
    "퇴직연금": PensionTypeCode.RETIREMENT_PENSION,
    "RETIREMENT_PENSION": PensionTypeCode.RETIREMENT_PENSION,
    "개인연금": PensionTypeCode.PERSONAL_PENSION,
    "PERSONAL_PENSION": PensionTypeCode.PERSONAL_PENSION,
    "연금저축": PensionTypeCode.PENSION_SAVINGS,
    "PENSION_SAVINGS": PensionTypeCode.PENSION_SAVINGS,
    "기관": PensionTypeCode.INSTITUTIONAL,
    "INSTITUTIONAL": PensionTypeCode.INSTITUTIONAL,
    "EMPLOYEE_WELFARE_PENSION": PensionTypeCode.EMPLOYEE_WELFARE_PENSION,
}


def _unknown(reason: str, *, method: str = "unknown") -> PensionTypeNormalization:
    return PensionTypeNormalization(
        pension_type_codes=[PensionTypeCode.UNKNOWN],
        pension_type_labels_ko=[LABELS_KO[PensionTypeCode.UNKNOWN]],
        normalization_method=method,  # type: ignore[arg-type]
        confidence=0.0,
        reason=reason,
        requires_review=True,
        normalizer_model=None,
        normalizer_prompt_version=PROMPT_VERSION,
    )


def _labels(codes: list[PensionTypeCode]) -> list[str]:
    return [LABELS_KO[code] for code in codes]


class ProductNormalizer:
    """Rule normalization, unique-value cache, and strictly fail-closed LLM fallback."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        settings: LLMNormalizerSettings | None = None,
    ) -> None:
        self.settings = settings or LLMNormalizerSettings.from_environment(provider)
        self.llm = ProductNormalizerLLM(provider, self.settings)
        self.cache: dict[str, PensionTypeNormalization] = {}
        self.normalizer_calls = 0
        self.llm_failures = 0

    @classmethod
    def from_environment(cls, provider: LLMProvider | None = None) -> "ProductNormalizer":
        return cls(provider=provider, settings=LLMNormalizerSettings.from_environment(provider))

    def cache_key(self, raw_value: str, schema_version: str = "0.1") -> str:
        payload = {
            "raw_value_hash": sha256(raw_value.encode("utf-8")).hexdigest(),
            "schema_version": schema_version,
            "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model_version": self.settings.model,
        }
        return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def normalize(self, context: RawPensionTypeContext) -> PensionTypeNormalization:
        direct = self._by_python_rule(context.pension_type_raw)
        if direct is not None:
            return direct
        if context.pension_type_raw is None or not str(context.pension_type_raw).strip():
            return _unknown("pension_type is missing", method="missing")

        raw_value = str(context.pension_type_raw)
        key = self.cache_key(raw_value, context.schema_version)
        if key in self.cache:
            return self.cache[key]
        if not self.settings.enabled:
            result = _unknown("LLM normalizer is disabled")
        elif self.normalizer_calls >= self.settings.max_calls:
            result = _unknown("LLM normalizer call budget exhausted")
        else:
            result = self._by_llm(context)
        self.cache[key] = result
        return result

    @staticmethod
    def _by_python_rule(raw_value: str | None) -> PensionTypeNormalization | None:
        if raw_value is None:
            return None
        normalized = str(raw_value).strip()
        if not normalized:
            return None
        # Comma-separated values preserve explicit multi-value source evidence.
        parts = [part.strip() for part in normalized.replace("，", ",").split(",")]
        if not parts or any(not part or part not in _DIRECT_CODES for part in parts):
            return None
        codes: list[PensionTypeCode] = []
        for part in parts:
            code = _DIRECT_CODES[part]
            if code not in codes:
                codes.append(code)
        return PensionTypeNormalization(
            pension_type_codes=codes,
            pension_type_labels_ko=_labels(codes),
            normalization_method="python_rule",
            confidence=1.0,
            reason="exact canonical value or explicitly comma-separated canonical values",
            requires_review=False,
            normalizer_model=None,
            normalizer_prompt_version=PROMPT_VERSION,
        )

    def _by_llm(self, context: RawPensionTypeContext) -> PensionTypeNormalization:
        try:
            self.normalizer_calls += 1
            output = self.llm.normalize(context)
            codes = list(output.pension_type_codes)
            raw = str(context.pension_type_raw or "")
            if PensionTypeCode.UNKNOWN in codes or not codes:
                raise ValueError("unknown_or_empty_code")
            if len(codes) > 1 and not any(separator in raw for separator in (",", "，", "/")):
                raise ValueError("unjustified_multiple_codes")
            explicit_codes = self._explicit_codes_in_context(context)
            if explicit_codes and set(codes) != explicit_codes:
                raise ValueError("source_evidence_conflicts_with_llm_output")
            if output.confidence < self.settings.confidence_threshold:
                raise ValueError("confidence_below_threshold")
            if output.requires_review:
                raise ValueError("requires_review")
            return PensionTypeNormalization(
                pension_type_codes=codes,
                pension_type_labels_ko=_labels(codes),
                normalization_method="llm",
                confidence=output.confidence,
                reason=output.reason,
                requires_review=False,
                normalizer_model=self.settings.model,
                normalizer_prompt_version=PROMPT_VERSION,
            )
        except Exception:
            self.llm_failures += 1
            return _unknown("LLM normalization could not be safely accepted")

    @staticmethod
    def _explicit_codes_in_context(context: RawPensionTypeContext) -> set[PensionTypeCode]:
        searchable = " ".join(
            str(value or "") for value in (context.class_name, context.eligibility_text)
        ).upper()
        matches: set[PensionTypeCode] = set()
        for phrase, code in _DIRECT_CODES.items():
            if phrase.upper() in searchable:
                matches.add(code)
        return matches

    def report(self) -> dict[str, int | str | bool | None]:
        return {
            "normalizer_calls": self.normalizer_calls,
            "llm_failures": self.llm_failures,
            "unique_unresolved_values": len(self.cache),
            "enabled": self.settings.enabled,
            "model": self.settings.model,
            "policy_version": NORMALIZATION_POLICY_VERSION,
            "prompt_version": PROMPT_VERSION,
        }
