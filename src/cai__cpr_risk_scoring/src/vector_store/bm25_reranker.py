from __future__ import annotations

from rank_bm25 import BM25Okapi

from src.models.schemas import DocumentChunk
from src.utils.logger import get_logger

logger = get_logger(__name__)

_RRF_K = 60  # standard constant for Reciprocal Rank Fusion


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


class BM25Reranker:
    """
    Re-ranks a candidate set of DocumentChunks using BM25 + Reciprocal Rank Fusion (RRF).

    Flow:
      1. Build a BM25 index over the candidate chunks (in-memory, no persistence needed).
      2. Score each candidate with BM25 against the original query.
      3. Combine BM25 rank and semantic similarity rank via RRF.
      4. Return top_k chunks sorted by RRF score descending.

    RRF formula: score(i) = 1/(K + rank_semantic(i)) + 1/(K + rank_bm25(i))
    K=60 is the standard value that prevents very high ranks from dominating.
    """

    def rerank(
        self,
        query: str,
        chunks: list[DocumentChunk],
        top_k: int,
    ) -> list[DocumentChunk]:
        if not chunks:
            return chunks
        if len(chunks) <= top_k:
            # Still re-rank even if fewer candidates than top_k — improves ordering
            top_k = len(chunks)

        corpus = [_tokenize(c.content) for c in chunks]
        bm25 = BM25Okapi(corpus)
        bm25_scores = bm25.get_scores(_tokenize(query))

        # Semantic rank — higher similarity_score = better rank (rank 0 = best)
        sem_order = sorted(
            range(len(chunks)),
            key=lambda i: chunks[i].similarity_score or 0.0,
            reverse=True,
        )
        sem_rank = {idx: rank for rank, idx in enumerate(sem_order)}

        # BM25 rank — higher score = better rank (rank 0 = best)
        bm25_order = sorted(
            range(len(chunks)),
            key=lambda i: bm25_scores[i],
            reverse=True,
        )
        bm25_rank = {idx: rank for rank, idx in enumerate(bm25_order)}

        rrf_scores = [
            1.0 / (_RRF_K + sem_rank[i]) + 1.0 / (_RRF_K + bm25_rank[i])
            for i in range(len(chunks))
        ]

        ranked = sorted(range(len(chunks)), key=lambda i: rrf_scores[i], reverse=True)
        result = [chunks[i] for i in ranked[:top_k]]

        logger.debug(
            "[BM25Reranker] Reranked %d → %d | top_rrf=%.4f | query='%s'",
            len(chunks), top_k, rrf_scores[ranked[0]] if ranked else 0.0, query[:60],
        )
        return result
