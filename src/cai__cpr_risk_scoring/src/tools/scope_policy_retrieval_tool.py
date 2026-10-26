import asyncio

from src.config.constants import SCOPE_UNFILTERED_LABEL
from src.config.settings import Settings
from src.models.schemas import DocumentChunk
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger(__name__)


class ScopePolicyRetrievalTool:
    """
    Queries fcpa_scope_policy_index filtered by the FCPA scope area identified by
    ScopeIdentifierAgent. Returns Risk Profile and Audit Guidance chunks for that
    specific scope area — max 2 chunks (one per chunk type).
    """

    def __init__(self, index_manager: VectorIndexManager, settings: Settings) -> None:
        self._index_manager = index_manager
        self._top_k = settings.top_k_scope_policy
        self._index_name = settings.fcpa_scope_policy_index_name
        logger.info(
            "[ScopePolicyRetrievalTool] Initialised | index=%s | top_k=%d",
            self._index_name, self._top_k,
        )

    def retrieve(self, query: str, scope_id: str) -> list[DocumentChunk]:
        # When no reliable scope was identified the supervisor passes the unfiltered
        # sentinel (or a blank scope). Search all scope areas by semantic relevance —
        # no scope_id filter — and widen top_k so the analyst still gets a useful spread.
        is_unfiltered = (
            not scope_id or scope_id.strip().upper() == SCOPE_UNFILTERED_LABEL
        )
        effective_scope = None if is_unfiltered else scope_id
        effective_top_k = max(self._top_k * 3, 6) if is_unfiltered else self._top_k

        logger.debug(
            "[ScopePolicyRetrievalTool] retrieve | scope=%s | unfiltered=%s | query='%s'",
            scope_id, is_unfiltered, query[:80],
        )
        chunks = self._index_manager.similarity_search(
            index_key="scope_policy",
            query=query,
            scope_id=effective_scope,
            top_k=effective_top_k,
        )
        logger.info(
            "[ScopePolicyRetrievalTool] Retrieved %d chunks | scope=%s | unfiltered=%s",
            len(chunks), scope_id, is_unfiltered,
        )
        return chunks

    async def aretrieve(self, query: str, scope_id: str) -> list[DocumentChunk]:
        logger.debug(
            "[ScopePolicyRetrievalTool] async retrieve | scope=%s | query='%s'",
            scope_id, query[:80],
        )
        return await asyncio.to_thread(self.retrieve, query, scope_id)
