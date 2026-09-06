from __future__ import annotations

from typing import Any, Protocol


class SpecificationSupervisor(Protocol):
    @property
    def model_version(self) -> str: ...
    def analyze(self, request: dict[str, Any]) -> dict[str, Any]: ...


class HyperClovaSpecificationSupervisor:
    """Create execution specifications through HyperCLOVA X."""

    def __init__(self, llm: Any, provider: Any | None = None) -> None:
        self.llm = llm
        self.provider = provider

    @property
    def model_version(self) -> str:
        return str(getattr(self.llm, "model", "unknown"))

    def analyze(self, request: dict[str, Any]) -> dict[str, Any]:
        system = (
            "You create execution specifications only. Return one JSON object only. "
            "Do not recommend products, calculate values, make legal conclusions, or write an answer. "
            "Keep the supplied route and tools unchanged. Use only the allowed schema fields."
        )
        if self.provider is not None and hasattr(self.provider, "structured"):
            return self.provider.structured(system, request)
        if hasattr(self.llm, "structured"):
            return self.llm.structured(system, request)
        raise RuntimeError("HyperCLOVA X structured output is unavailable")
