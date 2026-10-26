from abc import ABC, abstractmethod

from langchain_core.language_models import BaseChatModel


class BaseLLMProvider(ABC):
    """
    Abstract contract for any LLM provider.
    Swap Azure OpenAI for another provider by subclassing this — nothing else changes.
    """

    @abstractmethod
    def get_client(self) -> BaseChatModel:
        """Return an initialised, ready-to-use chat model client."""
