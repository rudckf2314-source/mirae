from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from chatbot.hyperclova_client import HyperClovaLLM
from chatbot.model_policy import SUPPORTED_MODELS
from .base import BaseLLMProvider

class HyperClovaChatModel(BaseChatModel):
    client: Any = Field(exclude=True, repr=False)
    max_tokens: int = 4096
    temperature: float = 0.1

    @property
    def _llm_type(self) -> str:
        return "hyperclova-x"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        roles = {"system": "system", "human": "user", "ai": "assistant"}
        converted = []
        for message in messages:
            if message.type not in roles or not isinstance(message.content, str):
                raise ValueError("Extraction requires text system/user/assistant messages")
            converted.append({"role": roles[message.type], "content": message.content})
        text = self.client.chat_messages(converted, max_tokens=self.max_tokens,
                                        temperature=self.temperature, stop=stop)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

class HyperClovaLLMProvider(BaseLLMProvider):
    def __init__(self, settings):
        self.settings = settings

    def get_chat_model(self) -> BaseChatModel:
        s = self.settings
        if s.clova_extraction_model not in SUPPORTED_MODELS:
            raise ValueError("Unsupported HyperCLOVA X extraction model")
        client = HyperClovaLLM(api_key=s.clova_studio_api_key, model=s.clova_extraction_model,
                              base_url=s.clova_base_url, timeout=s.llm_timeout)
        return HyperClovaChatModel(client=client, max_tokens=s.llm_max_tokens,
                                  temperature=s.llm_temperature)
