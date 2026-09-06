"""Bounded, validated LLM fallback for otherwise unresolved pension-type values."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from .llm_provider import LLMProvider
from .product_json_schema import LLMNormalizationOutput, PensionTypeCode, RawPensionTypeContext


PROMPT_VERSION = "pension-type-normalizer-v1"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


@dataclass(frozen=True)
class LLMNormalizerSettings:
    enabled: bool
    model: str | None
    max_calls: int
    confidence_threshold: float

    @classmethod
    def from_environment(cls, provider: LLMProvider | None = None) -> "LLMNormalizerSettings":
        configured_model = os.getenv("PENSION_NORMALIZER_MODEL", "").strip()
        return cls(
            enabled=_env_bool("PENSION_ENABLE_LLM_NORMALIZER"),
            model=configured_model or (getattr(provider, "model", None) if provider else None),
            max_calls=_env_int("PENSION_NORMALIZER_MAX_CALLS", 20),
            confidence_threshold=_env_float("PENSION_NORMALIZER_CONFIDENCE_THRESHOLD", 0.90),
        )


class ProductNormalizerLLM:
    """Calls the existing provider once; caller owns cache and budget decisions."""

    def __init__(self, provider: LLMProvider | None, settings: LLMNormalizerSettings) -> None:
        self.provider = provider
        self.settings = settings

    def normalize(self, context: RawPensionTypeContext) -> LLMNormalizationOutput:
        if self.provider is None:
            raise RuntimeError("normalizer_provider_unavailable")
        payload: dict[str, Any] = {
            "pension_type_raw": context.pension_type_raw,
            "field_path": "classes[].pension_type",
            "class_name": context.class_name,
            "eligibility_text": context.eligibility_text,
            "allowed_codes": [code.value for code in PensionTypeCode if code != PensionTypeCode.UNKNOWN],
            "product_name": context.product_name,
            "schema_version": context.schema_version,
        }
        system = (
            "Classify only the supplied pension_type evidence. Return a JSON object with "
            "pension_type_codes, confidence, reason, and requires_review. Do not infer fees, "
            "risk, online availability, eligibility, or a product recommendation. If evidence "
            "is insufficient, return OTHER with requires_review=true."
        )
        structured_for_model = getattr(self.provider, "structured_for_model", None)
        response = (
            structured_for_model(system, payload, self.settings.model)
            if callable(structured_for_model) and self.settings.model
            else self.provider.structured(system, payload)
        )
        return LLMNormalizationOutput.model_validate(response)
