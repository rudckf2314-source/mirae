from abc import ABC, abstractmethod

from langchain_core.language_models.chat_models import BaseChatModel


class BaseLLMProvider(ABC):
    @abstractmethod
    def get_chat_model(self) -> BaseChatModel:
        raise NotImplementedError
