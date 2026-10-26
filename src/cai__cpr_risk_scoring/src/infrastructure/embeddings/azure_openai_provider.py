from functools import cached_property

from langchain_core.embeddings import Embeddings
from langchain_openai import AzureOpenAIEmbeddings

from src.config.settings import Settings
from src.infrastructure.embeddings.base import BaseEmbeddingProvider


class AzureOpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """
    Azure OpenAI implementation of BaseEmbeddingProvider.
    Uses the configured embedding model and dimensions (set via AZURE_EMBEDDING_DEPLOYMENT / EMBEDDING_DIMENSIONS env vars).
    Client is cached after first access — one instance reused across vector store and services.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @cached_property
    def _client(self) -> AzureOpenAIEmbeddings:
        return AzureOpenAIEmbeddings(
            azure_endpoint=self._settings.azure_embedding_endpoint,
            azure_deployment=self._settings.azure_embedding_deployment,
            api_key=self._settings.azure_embedding_api_key,
            api_version=self._settings.azure_openai_api_version,
            dimensions=self._settings.embedding_dimensions,
            request_timeout=60,
        )

    def get_client(self) -> Embeddings:
        return self._client
