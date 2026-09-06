from typing import Any, Protocol

class LLMProvider(Protocol):
    model: str
    def answer_from_context(self, question: str, contexts: list[dict[str, Any]]) -> str: ...
    def answer_from_evidence(self, question: str, evidence_text: str) -> str: ...
    def structured(self, system: str, payload: dict[str, Any]) -> dict[str, Any]: ...

class HyperClovaProviderAdapter:
    def __init__(self, llm):
        self.llm, self.model = llm, llm.model
    def answer_from_context(self, question, contexts):
        return self.llm.generate(question, contexts)
    def answer_from_evidence(self, question, evidence_text):
        return self.llm.generate_from_evidence(question, evidence_text)
    def structured(self, system, payload):
        return self.llm.structured(system, payload)
    def structured_for_model(self, system, payload, model):
        if model != self.model:
            raise ValueError("PENSION_NORMALIZER_MODEL must match CLOVA_NORMALIZER_MODEL")
        return self.structured(system, payload)
