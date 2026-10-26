"""Direct-access vector index for questionnaire Q&A (the `questionnaire_index`).

Mirrors the create/upsert/query pattern of the main app's VectorIndexManager but with a
questionnaire-shaped schema, so ingestion can push every IDR / Planning Q&A with metadata and the
market-overview retrieval can query it back. It does not touch the main application's indexes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from databricks.vector_search.client import VectorSearchClient
from langchain_core.embeddings import Embeddings

from src.utils.logger import get_logger

from ..config import ScopingDeckConfig

logger = get_logger(__name__)


@dataclass
class QuestionnaireChunk:
    """One questionnaire Q&A ready to embed and upsert."""

    chunk_id: str
    content: str
    question_id: str
    question_text: str
    response_text: str
    followup_question: str
    followup_response: str
    scope_id: str
    auditor_id: str
    questionnaire_type: str
    source_table: str
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity_score: float | None = None


class QuestionnaireIndexManager:
    """Creates, populates, and queries the questionnaire vector index."""

    _COLUMNS = [
        "chunk_id", "content", "question_id", "question_text", "response_text",
        "followup_question", "followup_response", "scope_id", "auditor_id",
        "questionnaire_type", "source_table", "metadata_json",
    ]
    _SCHEMA = {
        "chunk_id": "string",
        "content": "string",
        "question_id": "string",
        "question_text": "string",
        "response_text": "string",
        "followup_question": "string",
        "followup_response": "string",
        "scope_id": "string",
        "auditor_id": "string",
        "questionnaire_type": "string",
        "source_table": "string",
        "metadata_json": "string",
        "embedding": "array<float>",
    }

    def __init__(self, config: ScopingDeckConfig, embeddings: Embeddings) -> None:
        self._settings = config.settings
        self._embeddings = embeddings
        self._client = VectorSearchClient(
            workspace_url=self._settings.databricks_host,
            service_principal_client_id=self._settings.databricks_client_id,
            service_principal_client_secret=self._settings.databricks_client_secret,
            disable_notice=True,
        )
        self._index_name = config.full_questionnaire_index
        self._endpoint = self._settings.databricks_vector_search_endpoint
        self._index: Any = None

    def ensure_index(self) -> None:
        """Create the index if absent, then block until it is query and write ready."""
        try:
            self._index = self._client.get_index(
                endpoint_name=self._endpoint, index_name=self._index_name
            )
            logger.info("[QIndex] Already exists | index=%s", self._index_name)
            return
        except Exception:
            pass

        logger.info("[QIndex] Creating | index=%s | endpoint=%s", self._index_name, self._endpoint)
        self._client.create_direct_access_index(
            endpoint_name=self._endpoint,
            index_name=self._index_name,
            primary_key="chunk_id",
            embedding_dimension=self._settings.embedding_dimensions,
            embedding_vector_column="embedding",
            schema=self._SCHEMA,
        )
        logger.info("[QIndex] Created | index=%s", self._index_name)

        for attempt in range(24):
            try:
                self._index = self._client.get_index(
                    endpoint_name=self._endpoint, index_name=self._index_name
                )
                break
            except Exception:
                logger.info("[QIndex] Waiting for metadata | attempt=%d/24", attempt + 1)
                time.sleep(5)
        else:
            raise RuntimeError(f"Index {self._index_name} not available 120s after creation")

        self._wait_until_ready(timeout_s=600)
        logger.info("[QIndex] Write/query ready | index=%s", self._index_name)

    def is_populated(self, auditor_id: str | None = None) -> bool:
        """Return True when the index holds at least one row for the given auditor."""
        try:
            index = self._index or self._client.get_index(
                endpoint_name=self._endpoint, index_name=self._index_name
            )
            filters = {"auditor_id": auditor_id} if auditor_id else None
            results = index.similarity_search(
                columns=["chunk_id"],
                query_vector=[1e-6] * self._settings.embedding_dimensions,
                filters=filters,
                num_results=1,
            )
            rows = results.get("result", {}).get("data_array", [])
            return len(rows) > 0
        except Exception as exc:
            logger.warning("[QIndex] is_populated check failed — treating as empty | %s", exc)
            return False

    def upsert(self, chunks: list[QuestionnaireChunk], batch_size: int = 50) -> None:
        """Embed and upsert the chunks in batches."""
        if not chunks:
            logger.warning("[QIndex] Nothing to upsert")
            return
        if self._index is None:
            raise RuntimeError("ensure_index() must be called before upsert()")

        total = len(chunks)
        batches = [chunks[i : i + batch_size] for i in range(0, total, batch_size)]
        logger.info("[QIndex] Upserting %d chunks in %d batch(es)", total, len(batches))

        done = 0
        for batch in batches:
            embeddings = self._embed_with_retry([c.content for c in batch])
            records = [self._to_record(chunk, emb) for chunk, emb in zip(batch, embeddings)]
            self._upsert_with_retry(records)
            done += len(batch)
            logger.info("[QIndex] Upserted %d/%d", done, total)

        logger.info("[QIndex] Upsert complete | total=%d | index=%s", total, self._index_name)

    def similarity_search(
        self,
        query: str,
        top_k: int = 6,
        auditor_id: str | None = None,
        questionnaire_type: str | None = None,
    ) -> list[QuestionnaireChunk]:
        """Return the top_k questionnaire chunks most similar to the query."""
        index = self._index or self._client.get_index(
            endpoint_name=self._endpoint, index_name=self._index_name
        )
        query_vec = self._embeddings.embed_query(query)

        filters: dict[str, Any] = {}
        if auditor_id:
            filters["auditor_id"] = auditor_id
        if questionnaire_type:
            filters["questionnaire_type"] = questionnaire_type

        results = index.similarity_search(
            columns=self._COLUMNS,
            query_vector=query_vec,
            filters=filters or None,
            num_results=top_k,
        )
        return [self._to_chunk(row) for row in results.get("result", {}).get("data_array", [])]

    def _wait_until_ready(self, timeout_s: int = 300) -> None:
        if self._index is None:
            return
        epsilon = [1e-6] * self._settings.embedding_dimensions
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                self._index.similarity_search(
                    columns=["chunk_id"], query_vector=epsilon, num_results=1
                )
                logger.info("[QIndex] Query-ready | index=%s", self._index_name)
                return
            except Exception as exc:
                if any(p in str(exc).lower() for p in ("not ready", "pending deletion", "does not exist")):
                    time.sleep(10)
                    continue
                return
        logger.warning("[QIndex] Not confirmed ready after %ds — proceeding", timeout_s)

    @staticmethod
    def _to_record(chunk: QuestionnaireChunk, embedding: list[float]) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "content": chunk.content,
            "question_id": chunk.question_id,
            "question_text": chunk.question_text,
            "response_text": chunk.response_text,
            "followup_question": chunk.followup_question,
            "followup_response": chunk.followup_response,
            "scope_id": chunk.scope_id,
            "auditor_id": chunk.auditor_id,
            "questionnaire_type": chunk.questionnaire_type,
            "source_table": chunk.source_table,
            "metadata_json": json.dumps(chunk.metadata, default=str),
            "embedding": list(embedding),
        }

    def _to_chunk(self, row: list[Any]) -> QuestionnaireChunk:
        data = dict(zip(self._COLUMNS, row))
        return QuestionnaireChunk(
            chunk_id=data.get("chunk_id", ""),
            content=data.get("content", ""),
            question_id=data.get("question_id", ""),
            question_text=data.get("question_text", ""),
            response_text=data.get("response_text", ""),
            followup_question=data.get("followup_question", ""),
            followup_response=data.get("followup_response", ""),
            scope_id=data.get("scope_id", ""),
            auditor_id=data.get("auditor_id", ""),
            questionnaire_type=data.get("questionnaire_type", ""),
            source_table=data.get("source_table", ""),
            metadata=json.loads(data.get("metadata_json") or "{}"),
            similarity_score=row[-1] if len(row) > len(self._COLUMNS) else None,
        )

    def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._embeddings.embed_documents(texts)
            except Exception as exc:
                last = exc
                logger.warning("[QIndex] embed_documents failed (%d/3) | %s", attempt + 1, exc)
                time.sleep(5)
        raise RuntimeError("embed_documents failed after 3 attempts") from last

    def _upsert_with_retry(self, records: list[dict]) -> None:
        for attempt in range(6):
            try:
                response = self._index.upsert(records)
                failed = (
                    response.get("result", {}).get("failed_primary_keys", [])
                    if isinstance(response, dict) else []
                )
                if failed:
                    raise RuntimeError(f"Upsert failed for {len(failed)}/{len(records)} chunks")
                return
            except Exception as exc:
                if "not ready" in str(exc).lower() and attempt < 5:
                    logger.info("[QIndex] Not ready on upsert — retry in 10s (%d/6)", attempt + 1)
                    time.sleep(10)
                    continue
                raise
