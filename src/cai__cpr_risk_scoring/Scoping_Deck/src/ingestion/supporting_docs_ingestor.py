"""Ensures the current auditor's supporting docs are present in the shared supporting index.

Reuses the risk-analysis pipeline's own idempotent helper against the SAME
`auditor_supporting_docs_index` the Market Overview retrieval reads from, so the deck is
self-sufficient when run standalone. Skips instantly when the auditor's docs are already embedded.
"""

from __future__ import annotations

from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

from ..config import ScopingDeckConfig

logger = get_logger(__name__)


class SupportingDocsIngestor:
    """Ensures the auditor's supporting docs exist in the shared supporting index."""

    def __init__(self, config: ScopingDeckConfig) -> None:
        self._settings = config.settings

    def ensure(self) -> None:
        """Ingest the auditor's supporting docs if not already embedded (idempotent)."""
        from src.pipeline.runner import _ensure_supporting_docs_index

        provider = AzureOpenAIEmbeddingProvider(self._settings)
        index_manager = VectorIndexManager(self._settings, provider)
        logger.info(
            "[Ingest] Ensuring supporting docs in shared index | auditor=%s",
            self._settings.auditor_id,
        )
        _ensure_supporting_docs_index(index_manager, self._settings, self._settings.auditor_id)
