import asyncio
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config.constants import SCOPE_UNFILTERED_LABEL
from src.config.settings import Settings
from src.models.schemas import DocumentChunk
from src.tools.base import BaseRetrievalTool
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager
from src.vector_store.multi_query_retriever import MultiQueryRetriever

logger = get_logger(__name__)


class PolicyRetrievalTool(BaseRetrievalTool):
    """
    Retrieves from the global scope-area policy index ("global_scope_area_policies").
    Applies MQR (multi-query expansion) when an LLM is provided so that different
    phrasings of a policy query surface the right SOP/guideline sections. Retrieval is
    scope-agnostic: when the unfiltered sentinel (or a blank scope) is passed, it searches
    across all scope areas by semantic relevance rather than filtering by scope_id.
    """

    def __init__(
        self,
        index_manager: VectorIndexManager,
        settings: Settings,
        llm: BaseChatModel | None = None,
    ) -> None:
        self._index_manager = index_manager
        self._top_k = settings.top_k_policy
        self._index_name = settings.policy_index_name
        self._mqr = MultiQueryRetriever(llm, n_alternatives=2) if llm is not None else None
        logger.info(
            "[PolicyRetrievalTool] Initialised | index=%s | top_k=%d | mqr=%s",
            self._index_name, self._top_k, "enabled" if self._mqr else "disabled",
        )

    def retrieve(
        self,
        query: str,
        scope_id: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        is_unfiltered = not scope_id or scope_id.strip().upper() == SCOPE_UNFILTERED_LABEL
        effective_scope = None if is_unfiltered else scope_id
        effective_top_k = top_k or (max(self._top_k * 3, 6) if is_unfiltered else self._top_k)

        def _search(q: str) -> list[DocumentChunk]:
            return self._index_manager.similarity_search(
                index_key="policy",
                query=q,
                scope_id=effective_scope,
                top_k=effective_top_k,
                filters=filters,
            )

        if self._mqr:
            return self._mqr.retrieve(query, _search, top_k=effective_top_k)
        return _search(query)

    async def aretrieve(
        self,
        query: str,
        scope_id: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        is_unfiltered = not scope_id or scope_id.strip().upper() == SCOPE_UNFILTERED_LABEL
        effective_scope = None if is_unfiltered else scope_id
        effective_top_k = top_k or (max(self._top_k * 3, 6) if is_unfiltered else self._top_k)

        def _search(q: str) -> list[DocumentChunk]:
            return self._index_manager.similarity_search(
                index_key="policy",
                query=q,
                scope_id=effective_scope,
                top_k=effective_top_k,
                filters=filters,
            )

        if self._mqr:
            return await self._mqr.aretrieve(query, _search, top_k=effective_top_k)
        return await asyncio.to_thread(_search, query)
