"""Role-specific HyperCLOVA X model selection; no cross-provider fallback."""
import os

ROLE_DEFAULTS = {
    "answer": "HCX-005",
    "supervisor": "HCX-007",
    "normalizer": "HCX-DASH-002",
    "extraction": "HCX-005",
}
SUPPORTED_MODELS = {"HCX-007", "HCX-005", "HCX-DASH-002"}


def model_for_role(role: str) -> str:
    default = ROLE_DEFAULTS[role]
    value = os.getenv(f"CLOVA_{role.upper()}_MODEL", "").strip() or default
    if value not in SUPPORTED_MODELS:
        raise ValueError(f"Unsupported HyperCLOVA X model for {role}: {value}")
    return value


def llm_for_role(role: str):
    from .hyperclova_client import HyperClovaLLM
    return HyperClovaLLM(model=model_for_role(role))
