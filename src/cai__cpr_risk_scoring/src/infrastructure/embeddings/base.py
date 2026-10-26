from abc import ABC, abstractmethod

from langchain_core.embeddings import Embeddings


class BaseEmbeddingProvider(ABC):
    """
    Abstract contract for any embedding model provider.
    Swap Azure OpenAI for another provider by subclassing this — nothing else changes.
    """

    @abstractmethod
    def get_client(self) -> Embeddings:
        """Return an initialised, ready-to-use embeddings client."""
