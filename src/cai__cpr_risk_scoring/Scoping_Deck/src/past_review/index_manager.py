"""Direct-access vector index for prior-review findings (`past_review_gacs_and_cpr_index`).

Stores each classified observation so the CPR / GACS slides can fetch every finding of a kind for the
auditor (not top-k), then group and summarise. Mirrors the questionnaire index manager's pattern.
"""

from __future__ import annotations

import json
import time
from typing import Any

from databricks.vector_search.client import VectorSearchClient
from langchain_core.embeddings import Embeddings

from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from .models import PastReviewObservation, ReviewKind

logger = get_logger(__name__)

_FETCH_ALL_LIMIT = 2000


class PastReviewIndexManager:
    """Creates, populates, and queries the prior-review vector index."""

    _COLUMNS = [
        "chunk_id", "content", "scope_area", "issue_description", "criticality",
        "issue_type", "source_document", "audit_report", "report_year", "review_kind",
        "auditor_id", "metadata_json",
    ]
    _SCHEMA = {
        "chunk_id": "string",
        "content": "string",
        "scope_area": "string",
        "issue_description": "string",
        "criticality": "string",
        "issue_type": "string",
        "source_document": "string",
        "audit_report": "string",
        "report_year": "int",
        "review_kind": "string",
        "auditor_id": "string",
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
        self._index_name = config.full_past_review_index
        self._endpoint = self._settings.databricks_vector_search_endpoint
        self._index: Any = None

    def ensure_index(self) -> None:
        """Create the index if absent, then block until it is query and write ready."""
        try:
            self._index = self._client.get_index(
                endpoint_name=self._endpoint, index_name=self._index_name
            )
            logger.info("[PRIndex] Already exists | index=%s", self._index_name)
            return
        except Exception:
            pass

        logger.info("[PRIndex] Creating | index=%s", self._index_name)
        self._client.create_direct_access_index(
            endpoint_name=self._endpoint,
            index_name=self._index_name,
            primary_key="chunk_id",
            embedding_dimension=self._settings.embedding_dimensions,
            embedding_vector_column="embedding",
            schema=self._SCHEMA,
        )
        for attempt in range(24):
            try:
                self._index = self._client.get_index(
                    endpoint_name=self._endpoint, index_name=self._index_name
                )
                break
            except Exception:
                logger.info("[PRIndex] Waiting for metadata | attempt=%d/24", attempt + 1)
                time.sleep(5)
        else:
            raise RuntimeError(f"Index {self._index_name} not available 120s after creation")
        self._wait_until_ready(timeout_s=600)
        logger.info("[PRIndex] Write/query ready | index=%s", self._index_name)

    def is_populated(self, auditor_id: str) -> bool:
        """Return True when the index already holds rows for the auditor."""
        try:
            index = self._index or self._client.get_index(
                endpoint_name=self._endpoint, index_name=self._index_name
            )
            results = index.similarity_search(
                columns=["chunk_id"],
                query_vector=[1e-6] * self._settings.embedding_dimensions,
                filters={"auditor_id": auditor_id},
                num_results=1,
            )
            return len(results.get("result", {}).get("data_array", [])) > 0
        except Exception as exc:
            logger.warning("[PRIndex] is_populated check failed — treating as empty | %s", exc)
            return False

    def upsert(self, observations: list[PastReviewObservation], auditor_id: str) -> None:
        """Embed and upsert the observations, tagged with the auditor id."""
        if not observations:
            logger.warning("[PRIndex] Nothing to upsert")
            return
        if self._index is None:
            raise RuntimeError("ensure_index() must be called before upsert()")

        records = []
        contents = [f"{o.scope_area}\n{o.issue_description}" for o in observations]
        embeddings = self._embed_with_retry(contents)
        for i, (obs, emb) in enumerate(zip(observations, embeddings)):
            records.append({
                "chunk_id": f"pr::{auditor_id}::{i}",
                "content": contents[i],
                "scope_area": obs.scope_area,
                "issue_description": obs.issue_description,
                "criticality": obs.criticality,
                "issue_type": obs.issue_type,
                "source_document": obs.source_document,
                "audit_report": obs.audit_report,
                "report_year": obs.report_year or 0,
                "review_kind": obs.review_kind.value,
                "auditor_id": auditor_id,
                "metadata_json": json.dumps({"report_year": obs.report_year}, default=str),
                "embedding": list(emb),
            })
        self._upsert_with_retry(records)
        logger.info("[PRIndex] Upserted %d observation(s) | index=%s", len(records), self._index_name)

    def fetch_by_kind(self, auditor_id: str, kind: ReviewKind) -> list[PastReviewObservation]:
        """Return all observations of the given kind for the auditor."""
        index = self._index or self._client.get_index(
            endpoint_name=self._endpoint, index_name=self._index_name
        )
        results = index.similarity_search(
            columns=self._COLUMNS,
            query_vector=[1e-6] * self._settings.embedding_dimensions,
            filters={"auditor_id": auditor_id, "review_kind": kind.value},
            num_results=_FETCH_ALL_LIMIT,
        )
        observations = [self._to_observation(row) for row in results.get("result", {}).get("data_array", [])]
        logger.info("[PRIndex] Fetched %d %s observation(s)", len(observations), kind.value)
        return observations

    def _to_observation(self, row: list[Any]) -> PastReviewObservation:
        data = dict(zip(self._COLUMNS, row))
        year = data.get("report_year")
        return PastReviewObservation(
            scope_area=data.get("scope_area", ""),
            issue_description=data.get("issue_description", ""),
            criticality=data.get("criticality", ""),
            issue_type=data.get("issue_type", ""),
            source_document=data.get("source_document", ""),
            audit_report=data.get("audit_report", ""),
            report_year=int(year) if isinstance(year, (int, float)) and year else None,
            review_kind=ReviewKind(data.get("review_kind", "other")),
        )

    def _wait_until_ready(self, timeout_s: int = 300) -> None:
        if self._index is None:
            return
        epsilon = [1e-6] * self._settings.embedding_dimensions
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                self._index.similarity_search(columns=["chunk_id"], query_vector=epsilon, num_results=1)
                return
            except Exception as exc:
                if any(p in str(exc).lower() for p in ("not ready", "pending deletion", "does not exist")):
                    time.sleep(10)
                    continue
                return
        logger.warning("[PRIndex] Not confirmed ready after %ds — proceeding", timeout_s)

    def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._embeddings.embed_documents(texts)
            except Exception as exc:
                last = exc
                logger.warning("[PRIndex] embed_documents failed (%d/3) | %s", attempt + 1, exc)
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
                    logger.info("[PRIndex] Not ready on upsert — retry in 10s (%d/6)", attempt + 1)
                    time.sleep(10)
                    continue
                raise
