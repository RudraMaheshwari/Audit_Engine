"""Retrieves and assembles the grounded market context for the Market Overview generator.

Merges questionnaire chunks (from the questionnaire index) with optional supporting-doc snippets
(from the shared supporting index) into a single context block, deduplicated and capped.
"""

from __future__ import annotations

from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

from ..config import ScopingDeckConfig
from ..ingestion.questionnaire_index import QuestionnaireChunk, QuestionnaireIndexManager

logger = get_logger(__name__)


class MarketContextRetriever:
    """Builds the grounded context block from questionnaire and supporting-doc retrieval."""

    _SEED_QUERIES = [
        "healthcare market and healthcare system in the country where the entity operates",
        "government or state ownership of hospitals, payers, and healthcare customers",
        "how products are sold and distributed: distributors, dealers, sub-dealers, tenders, direct sales",
        "customers, healthcare professionals (HCPs), and government officials interactions",
        "third party intermediaries, agents, and commercial partners used in the market",
        "regulatory environment, anti-bribery and anti-corruption laws applicable in the country",
        "public vs private hospital mix and procurement / tender process",
        "market size, competitive landscape, and business model of the entity",
    ]

    def __init__(self, config: ScopingDeckConfig, index: QuestionnaireIndexManager) -> None:
        self._config = config
        self._index = index

    def build_context_block(self) -> str:
        """Return the assembled context block for the current auditor."""
        auditor_id = self._config.settings.auditor_id
        chunks = self._retrieve_questionnaire_chunks(auditor_id)
        support = self._retrieve_supporting_snippets(auditor_id)
        return self._assemble(chunks, support)

    def get_chunks(self) -> list[QuestionnaireChunk]:
        """Return the raw questionnaire chunks retrieved for the current auditor."""
        return self._retrieve_questionnaire_chunks(self._config.settings.auditor_id)

    def _retrieve_questionnaire_chunks(self, auditor_id: str) -> list[QuestionnaireChunk]:
        merged: dict[str, QuestionnaireChunk] = {}
        for query in self._SEED_QUERIES:
            try:
                hits = self._index.similarity_search(
                    query=query, top_k=self._config.retrieval_top_k, auditor_id=auditor_id
                )
            except Exception as exc:
                logger.warning("[MarketOverview] Retrieval failed for seed '%s' | %s", query[:40], exc)
                continue
            for chunk in hits:
                merged.setdefault(chunk.chunk_id, chunk)
        chunks = list(merged.values())[: self._config.max_context_chunks]
        logger.info("[MarketOverview] Retrieved %d unique questionnaire chunk(s)", len(chunks))
        return chunks

    def _retrieve_supporting_snippets(self, auditor_id: str) -> list[str]:
        if not self._config.include_supporting_docs:
            return []
        try:
            provider = AzureOpenAIEmbeddingProvider(self._config.settings)
            index_manager = VectorIndexManager(self._config.settings, provider)
            snippets: list[str] = []
            seen: set[str] = set()
            for query in self._SEED_QUERIES:
                hits = index_manager.similarity_search(
                    index_key="supporting",
                    query=query,
                    scope_id=None,
                    top_k=self._config.supporting_docs_top_k,
                    filters={"auditor_folder": auditor_id},
                )
                for chunk in hits:
                    if chunk.chunk_id not in seen and chunk.content.strip():
                        seen.add(chunk.chunk_id)
                        snippets.append(f"[{chunk.document_name}] {chunk.content.strip()}")
            logger.info("[MarketOverview] Retrieved %d supporting-doc snippet(s)", len(snippets))
            return snippets[: self._config.max_context_chunks]
        except Exception as exc:
            logger.warning("[MarketOverview] Supporting-docs retrieval skipped | %s", exc)
            return []

    @staticmethod
    def _assemble(chunks: list[QuestionnaireChunk], support: list[str]) -> str:
        parts = [f"[{c.questionnaire_type.upper()} | {c.question_id}]\n{c.content}" for c in chunks]
        if support:
            parts.append("--- SUPPORTING DOCUMENTS ---")
            parts.extend(support)
        return "\n\n".join(parts) if parts else "(No audit context was retrieved.)"
