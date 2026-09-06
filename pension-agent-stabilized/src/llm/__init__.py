from config.settings import Settings
from .base import BaseLLMProvider
from .hyperclova import HyperClovaLLMProvider

def get_llm_provider(settings: Settings) -> BaseLLMProvider:
    return HyperClovaLLMProvider(settings)
