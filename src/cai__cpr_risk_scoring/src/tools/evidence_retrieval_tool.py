import asyncio
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config.settings import Settings
from src.models.schemas import DocumentChunk
from src.tools.base import BaseRetrievalTool
from src.utils.logger import get_logger
from src.vector_store.bm25_reranker import BM25Reranker
from src.vector_store.index_manager import VectorIndexManager
from src.vector_store.multi_query_retriever import MultiQueryRetriever

logger = get_logger(__name__)

_BM25_CANDIDATE_MULTIPLIER = 4  # fetch top_k * 4 candidates for BM25 to re-rank


class EvidenceRetrievalTool(BaseRetrievalTool):
    """
    Retrieves from the auditor supporting documents index (auditor_supporting_docs_index).

    Retrieval pipeline:
      1. MQR expands the query into 3 alternative phrasings.
      2. Each phrasing runs semantic search filtered by auditor_folder (current auditor only).
      3. Candidates are deduplicated across queries (MQR), keeping the best semantic score.
      4. BM25 re-ranks the candidate pool using RRF fusion with semantic scores.
      5. Returns top_k re-ranked chunks.

    Two modes:
      retrieve()          — broad MQR + BM25 search across the auditor's evidence
      retrieve_targeted() — filtered to a specific document name; skips MQR, applies BM25
    """

    def __init__(
        self,
        index_manager: VectorIndexManager,
        settings: Settings,
        auditor_folder: str,
        llm: BaseChatModel | None = None,
    ) -> None:
        self._index_manager = index_manager
        self._top_k = settings.top_k_supporting
        # Single-document (targeted) retrieval gets a higher cap so a large cited file is well covered.
        self._top_k_targeted = getattr(settings, "top_k_supporting_targeted", self._top_k * 6)
        self._index_name = settings.supporting_docs_index_name
        self._auditor_folder = auditor_folder
        self._mqr = MultiQueryRetriever(llm, n_alternatives=2) if llm is not None else None
        self._bm25 = BM25Reranker()
        logger.info(
            "[EvidenceRetrievalTool] Initialised | index=%s | top_k=%d | "
            "auditor=%s | mqr=%s | bm25=enabled",
            self._index_name, self._top_k, self._auditor_folder,
            "enabled" if self._mqr else "disabled",
        )

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        effective_top_k = top_k or self._top_k
        candidate_k = effective_top_k * _BM25_CANDIDATE_MULTIPLIER

        # Always filter to current auditor's folder
        base_filters: dict[str, Any] = {"auditor_folder": self._auditor_folder}
        if filters:
            base_filters.update(filters)

        def _search(q: str) -> list[DocumentChunk]:
            return self._index_manager.similarity_search(
                index_key="supporting",
                query=q,
                top_k=candidate_k,
                filters=base_filters,
            )

        # MQR → candidate pool
        if self._mqr:
            candidates = self._mqr.retrieve(query, _search, top_k=candidate_k)
        else:
            candidates = _search(query)

        # BM25 re-rank candidates → final top_k
        return self._bm25.rerank(query, candidates, top_k=effective_top_k)

    def retrieve_targeted(
        self,
        query: str,
        document_name: str,
        top_k: int | None = None,
    ) -> list[DocumentChunk]:
        """Targeted retrieval — filtered to a specific file cited in the response."""
        logger.debug(
            "[EvidenceRetrievalTool] targeted | auditor=%s | doc='%s' | query='%s'",
            self._auditor_folder, document_name, query[:80],
        )
        effective_top_k = top_k or self._top_k_targeted
        candidate_k = effective_top_k * _BM25_CANDIDATE_MULTIPLIER

        candidates = self.retrieve(
            query=query,
            top_k=candidate_k,
            filters={"document_name": document_name},
        )
        return self._bm25.rerank(query, candidates, top_k=effective_top_k)

    async def aretrieve(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        effective_top_k = top_k or self._top_k
        candidate_k = effective_top_k * _BM25_CANDIDATE_MULTIPLIER

        base_filters: dict[str, Any] = {"auditor_folder": self._auditor_folder}
        if filters:
            base_filters.update(filters)

        def _search(q: str) -> list[DocumentChunk]:
            return self._index_manager.similarity_search(
                index_key="supporting",
                query=q,
                top_k=candidate_k,
                filters=base_filters,
            )

        if self._mqr:
            candidates = await self._mqr.aretrieve(query, _search, top_k=candidate_k)
        else:
            candidates = await asyncio.to_thread(_search, query)

        return self._bm25.rerank(query, candidates, top_k=effective_top_k)

    async def aretrieve_targeted(
        self,
        query: str,
        document_name: str,
        top_k: int | None = None,
    ) -> list[DocumentChunk]:
        logger.debug(
            "[EvidenceRetrievalTool] async targeted | auditor=%s | doc='%s' | query='%s'",
            self._auditor_folder, document_name, query[:80],
        )
        effective_top_k = top_k or self._top_k_targeted
        candidate_k = effective_top_k * _BM25_CANDIDATE_MULTIPLIER

        candidates = await self.aretrieve(
            query=query,
            top_k=candidate_k,
            filters={"document_name": document_name},
        )
        return self._bm25.rerank(query, candidates, top_k=effective_top_k)
