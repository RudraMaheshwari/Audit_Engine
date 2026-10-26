import asyncio
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config.constants import HCBI_SCOPE_ID
from src.config.settings import Settings
from src.models.schemas import DocumentChunk
from src.tools.base import BaseRetrievalTool
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager
from src.vector_store.multi_query_retriever import MultiQueryRetriever

logger = get_logger(__name__)


class HcbiRetrievalTool(BaseRetrievalTool):
    """
    Retrieves from the HCBI Global Policy index (hcbi_policy_index).
    Applies MQR (multi-query expansion) when an LLM is provided so that
    different phrasings of a compliance requirement surface the right HCBI sections.
    Always filters by scope_id='global' — the partition key for all HCBI chunks.
    """

    def __init__(
        self,
        index_manager: VectorIndexManager,
        settings: Settings,
        llm: BaseChatModel | None = None,
    ) -> None:
        self._index_manager = index_manager
        self._top_k = settings.top_k_hcbi
        self._index_name = settings.hcbi_index_name
        self._mqr = MultiQueryRetriever(llm, n_alternatives=2) if llm is not None else None
        logger.info(
            "[HcbiRetrievalTool] Initialised | index=%s | top_k=%d | mqr=%s",
            self._index_name, self._top_k, "enabled" if self._mqr else "disabled",
        )

    def retrieve(
        self,
        query: str,
        scope_id: str = HCBI_SCOPE_ID,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        effective_top_k = top_k or self._top_k

        def _search(q: str) -> list[DocumentChunk]:
            return self._index_manager.similarity_search(
                index_key="hcbi",
                query=q,
                scope_id=HCBI_SCOPE_ID,
                top_k=effective_top_k,
                filters=filters,
            )

        if self._mqr:
            return self._mqr.retrieve(query, _search, top_k=effective_top_k)
        return _search(query)

    async def aretrieve(
        self,
        query: str,
        scope_id: str = HCBI_SCOPE_ID,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        effective_top_k = top_k or self._top_k

        def _search(q: str) -> list[DocumentChunk]:
            return self._index_manager.similarity_search(
                index_key="hcbi",
                query=q,
                scope_id=HCBI_SCOPE_ID,
                top_k=effective_top_k,
                filters=filters,
            )

        if self._mqr:
            return await self._mqr.aretrieve(query, _search, top_k=effective_top_k)
        return await asyncio.to_thread(_search, query)
