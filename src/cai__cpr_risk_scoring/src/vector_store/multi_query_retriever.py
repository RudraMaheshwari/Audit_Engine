from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from langchain_core.language_models import BaseChatModel

from src.models.schemas import DocumentChunk
from src.utils.logger import get_logger

logger = get_logger(__name__)

_SYSTEM = """\
You are a compliance evidence search specialist.

Given a search query about an audit response, generate {n} alternative phrasings \
that a supporting document in a corporate archive might actually use.

Think about:
- Official document/report names and form titles
- Corporate, HR, Finance, and Compliance terminology and abbreviations
- Different ways the same process, control, or activity might be described
- Acronyms common in multinational pharma/medtech audit contexts (HCC, FCPA, HCCO, \
  TPI, IDR, GL, FMV, LMS, JML, COI, SOX, etc.)

Return ONLY a valid JSON array of {n} strings. No markdown, no explanation.\
"""


class MultiQueryRetriever:
    """
    Wraps any sync similarity-search function with multi-query expansion.

    Usage:
        mqr = MultiQueryRetriever(llm, n_alternatives=2)
        chunks = mqr.retrieve(query, search_fn, top_k=5)
    """

    def __init__(self, llm: BaseChatModel, n_alternatives: int = 3) -> None:
        self._llm = llm
        self._n = n_alternatives

    def _generate_alternatives(self, query: str) -> list[str]:
        prompt = _SYSTEM.format(n=self._n) + f"\n\nQuery: {query}"
        try:
            msg = self._llm.invoke(prompt)
            alternatives = json.loads(msg.content)
            if isinstance(alternatives, list) and alternatives:
                unique = [str(q) for q in alternatives[: self._n] if str(q).strip() and str(q) != query]
                logger.info(
                    "[MQR] Generated %d alternatives | original='%s'",
                    len(unique), query[:60],
                )
                return [query] + unique
        except Exception as exc:
            logger.debug("[MQR] Alternative generation failed — falling back to original | error=%s", exc)
        return [query]

    async def _agenerate_alternatives(self, query: str) -> list[str]:
        prompt = _SYSTEM.format(n=self._n) + f"\n\nQuery: {query}"
        try:
            msg = await self._llm.ainvoke(prompt)
            alternatives = json.loads(msg.content)
            if isinstance(alternatives, list) and alternatives:
                unique = [str(q) for q in alternatives[: self._n] if str(q).strip() and str(q) != query]
                logger.info(
                    "[MQR] Generated %d alternatives (async) | original='%s'",
                    len(unique), query[:60],
                )
                return [query] + unique
        except Exception as exc:
            logger.debug("[MQR] Async alternative generation failed — falling back to original | error=%s", exc)
        return [query]

    def retrieve(
        self,
        query: str,
        search_fn: Callable[[str], list[DocumentChunk]],
        top_k: int = 5,
    ) -> list[DocumentChunk]:
        """
        1. Generate N alternative phrasings of query.
        2. Run all (original + alternatives) through search_fn in parallel.
        3. Deduplicate by chunk_id keeping the highest similarity score.
        4. Return top_k by score.
        """
        queries = self._generate_alternatives(query)

        best: dict[str, DocumentChunk] = {}
        with ThreadPoolExecutor(max_workers=len(queries)) as pool:
            futures = {pool.submit(search_fn, q): q for q in queries}
            for fut in as_completed(futures):
                try:
                    for chunk in fut.result():
                        prev = best.get(chunk.chunk_id)
                        if prev is None or (chunk.similarity_score or 0.0) > (prev.similarity_score or 0.0):
                            best[chunk.chunk_id] = chunk
                except Exception as exc:
                    logger.debug(
                        "[MQR] Search failed | query='%s' | error=%s",
                        futures[fut][:50], exc,
                    )

        ranked = sorted(best.values(), key=lambda c: c.similarity_score or 0.0, reverse=True)
        logger.info(
            "[MQR] Result: %d unique chunks from %d queries | top_score=%.3f",
            len(ranked), len(queries), ranked[0].similarity_score if ranked else 0.0,
        )
        return ranked[:top_k]

    async def aretrieve(
        self,
        query: str,
        search_fn: Callable[[str], list[DocumentChunk]],
        top_k: int = 5,
    ) -> list[DocumentChunk]:
        """
        Async version: generates alternatives with ainvoke, then runs all searches
        concurrently via asyncio.gather using asyncio.to_thread for blocking search_fn.
        """
        queries = await self._agenerate_alternatives(query)

        results = await asyncio.gather(
            *[asyncio.to_thread(search_fn, q) for q in queries],
            return_exceptions=True,
        )

        best: dict[str, DocumentChunk] = {}
        for q, result in zip(queries, results):
            if isinstance(result, Exception):
                logger.debug("[MQR] Async search failed | query='%s' | error=%s", q[:50], result)
                continue
            for chunk in result:
                prev = best.get(chunk.chunk_id)
                if prev is None or (chunk.similarity_score or 0.0) > (prev.similarity_score or 0.0):
                    best[chunk.chunk_id] = chunk

        ranked = sorted(best.values(), key=lambda c: c.similarity_score or 0.0, reverse=True)
        logger.info(
            "[MQR] Async result: %d unique chunks from %d queries | top_score=%.3f",
            len(ranked), len(queries), ranked[0].similarity_score if ranked else 0.0,
        )
        return ranked[:top_k]
