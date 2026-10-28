import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from databricks.vector_search.client import VectorSearchClient
from langchain_core.embeddings import Embeddings

from src.config.settings import Settings
from src.infrastructure.embeddings.base import BaseEmbeddingProvider
from src.models.schemas import DocumentChunk, DocumentSource
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Direct-access index lifecycle tuning (named so they're not scattered magic literals).
_PROBE_EPSILON = 1e-6          # near-zero query vector used only to test readiness / existence
_INDEX_POLL_ATTEMPTS = 24      # metadata-available / delete-propagation polls after create/delete

# Page-number and section keys vary across ingested policies (metadata_json is not uniform),
# which is why citations printed "Page No.: Not Available". Normalise by trying all variants.
_PAGE_KEYS = ("page_number", "page", "page_no", "pageNumber", "page_num", "pageno", "page_label")
_SECTION_KEYS = ("section", "section_no", "section_title", "section_number", "section_name", "section_heading")


def _first_nonempty(meta: dict, keys) -> str:
    for k in keys:
        v = meta.get(k)
        if v is not None and str(v).strip() and str(v).strip().lower() not in ("none", "null", "nan"):
            return str(v).strip()
    return ""


def _to_str(val) -> str:
    if val is None:
        return ""
    if hasattr(val, "value"):
        return str(val.value)
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _json_safe(val):
    if val is None:
        return None
    if hasattr(val, "value"):
        return val.value
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


_INDEX_CONFIG: dict[str, dict[str, Any]] = {
    "hcbi": {
        "source": DocumentSource.HCBI,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "policy": {
        "source": DocumentSource.POLICY,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "supporting": {
        "source": DocumentSource.SUPPORTING,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "scope_policy": {
        "source": DocumentSource.SCOPE_POLICY,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "transactional_analysis": {
        "source": DocumentSource.TRANSACTIONAL_ANALYSIS,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "past_audit": {
        "source": DocumentSource.PAST_AUDIT,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "si_history": {
        "source": DocumentSource.SI_HISTORY,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "legal": {
        "source": DocumentSource.LEGAL,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "additional_input": {
        "source": DocumentSource.ADDITIONAL_INPUT,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "local_policies": {
        "source": DocumentSource.LOCAL_POLICY,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    "raw_transactional": {
        "source": DocumentSource.RAW_TRANSACTIONAL,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
    # SHARED with the Scoping Deck — its own questionnaire-shaped schema (see search_questionnaire).
    "questionnaire": {
        "source": DocumentSource.QUESTIONNAIRE,
        "primary_key": "chunk_id",
        "embedding_col": "embedding",
        "text_col": "content",
    },
}

# The shared questionnaire index uses the Scoping Deck's schema (not the standard chunk schema), so it
# is created / written / queried through dedicated paths, not the generic ensure_index / similarity_search.
# The MAIN app is the PRODUCER (populates it from this auditor's Q&A); the Scoping Deck consumes it.
_QUESTIONNAIRE_COLUMNS = [
    "chunk_id", "content", "question_id", "question_text", "response_text",
    "followup_question", "followup_response", "scope_id", "auditor_id",
    "questionnaire_type", "source_table", "metadata_json",
]
_QUESTIONNAIRE_SCHEMA = {
    "chunk_id": "string", "content": "string", "question_id": "string",
    "question_text": "string", "response_text": "string",
    "followup_question": "string", "followup_response": "string",
    "scope_id": "string", "auditor_id": "string",
    "questionnaire_type": "string", "source_table": "string",
    "metadata_json": "string", "embedding": "array<float>",
}


class VectorIndexManager:
    """
    Manages Databricks Vector Search direct-access indexes for HCBI, policy, and
    supporting evidence. Provides create, upsert, similarity-search, and delete
    operations. Index objects are cached after first access to avoid repeated
    API calls across the pipeline.
    """

    def __init__(self, settings: Settings, embedding_provider: BaseEmbeddingProvider) -> None:
        self._settings = settings
        self._client = VectorSearchClient(
            workspace_url=settings.databricks_host,
            service_principal_client_id=settings.databricks_client_id,
            service_principal_client_secret=settings.databricks_client_secret,
            disable_notice=True,
        )
        self._embeddings: Embeddings = embedding_provider.get_client()
        _prefix = f"{settings.databricks_catalog}.{settings.databricks_schema_name}"
        self._index_names = {
            "hcbi":         f"{_prefix}.{settings.hcbi_index_name}",
            "policy":       f"{_prefix}.{settings.policy_index_name}",
            "supporting":   f"{_prefix}.{settings.supporting_docs_index_name}",
            "scope_policy": f"{_prefix}.{settings.fcpa_scope_policy_index_name}",
            "transactional_analysis": f"{_prefix}.{settings.transactional_analysis_index_name}",
            "past_audit":             f"{_prefix}.{settings.past_audit_index_name}",
            "si_history":             f"{_prefix}.{settings.si_history_index_name}",
            "legal":                  f"{_prefix}.{settings.legal_index_name}",
            "additional_input":       f"{_prefix}.{settings.additional_input_index_name}",
            "local_policies":         f"{_prefix}.{settings.local_policies_index_name}",
            "raw_transactional":      f"{_prefix}.{settings.raw_transactional_tables_index_name}",
            "questionnaire":          f"{_prefix}.{settings.questionnaire_index_name}",
        }
        self._index_cache: dict[str, Any] = {}

    def ensure_index(self, index_key: str) -> None:
        """Create the index if it does not exist, wait until ready, and cache the index object."""
        if index_key not in self._index_names:
            raise ValueError(f"Unknown index key: {index_key!r}")

        index_name = self._index_names[index_key]
        endpoint   = self._settings.databricks_vector_search_endpoint

        try:
            idx = self._client.get_index(endpoint_name=endpoint, index_name=index_name)
            self._index_cache[index_key] = idx
            logger.info("[VectorIndex] Already exists | index=%s", index_name)
            return
        except Exception:
            pass

        logger.info("[VectorIndex] Creating | index=%s | endpoint=%s", index_name, endpoint)
        self._client.create_direct_access_index(
            endpoint_name=endpoint,
            index_name=index_name,
            primary_key="chunk_id",
            embedding_dimension=self._settings.embedding_dimensions,
            embedding_vector_column="embedding",
            schema={
                "chunk_id":        "string",
                "document_name":   "string",
                "source":          "string",
                "content":         "string",
                "token_count":     "int",
                "scope_id":        "string",
                "auditor_folder":  "string",
                "embedding":       "array<float>",
                "document_type":   "string",
                "effective_date":  "string",
                "country":         "string",
                "policy_number":   "string",
                "policy_domain":   "string",
                "section_no":      "string",
                "metadata_json":   "string",
            },
        )
        logger.info("[VectorIndex] Created | index=%s", index_name)

        for attempt in range(_INDEX_POLL_ATTEMPTS):
            try:
                idx = self._client.get_index(endpoint_name=endpoint, index_name=index_name)
                self._index_cache[index_key] = idx
                logger.info("[VectorIndex] Index metadata available | index=%s", index_name)
                break
            except Exception:
                logger.info(
                    "[VectorIndex] Waiting for index to become available | index=%s | attempt=%d/24",
                    index_name, attempt + 1,
                )
                time.sleep(5)
        else:
            raise RuntimeError(f"Index {index_name} not available after 120s post-creation")

        # A freshly created index reports metadata almost immediately but the data plane is still
        # provisioning — it cannot accept writes/queries yet. Block until it is truly query-ready
        # so the ingestion upserts that follow do not fail with "Vector index ... is not ready".
        # A brand-new endpoint can take several minutes on the first index, so allow up to 10 min.
        self.wait_until_ready(index_key, timeout_s=600)
        logger.info("[VectorIndex] Index write/query ready | index=%s", index_name)

    def delete_index(self, index_key: str) -> None:
        index_name = self._index_names.get(index_key)
        if not index_name:
            return
        endpoint = self._settings.databricks_vector_search_endpoint
        self._index_cache.pop(index_key, None)
        try:
            self._client.delete_index(endpoint_name=endpoint, index_name=index_name)
            logger.info("[VectorIndex] Delete requested | index=%s", index_name)
        except Exception as exc:
            logger.warning(
                "[VectorIndex] Delete skipped (index may not exist) | index=%s | error=%s",
                index_name, exc,
            )
            return

        for attempt in range(_INDEX_POLL_ATTEMPTS):
            try:
                self._client.get_index(endpoint_name=endpoint, index_name=index_name)
            except Exception:
                logger.info("[VectorIndex] Delete confirmed | index=%s", index_name)
                return
            logger.info(
                "[VectorIndex] Waiting for delete to propagate | index=%s | attempt=%d/24",
                index_name, attempt + 1,
            )
            time.sleep(5)
        logger.warning(
            "[VectorIndex] Delete not confirmed after 120s — proceeding anyway | index=%s",
            index_name,
        )

    def delete_by_auditor_folder(
        self, index_key: str, auditor_folder: str, batch: int = 1000, max_rounds: int = 500
    ) -> int:
        """Delete ALL chunks for a given auditor_folder from the index.

        Repeatedly fetches up to `batch` chunks matching the auditor_folder filter and deletes
        them by primary key, looping until none remain. Self-terminating and complete regardless
        of any single-query result cap. Returns the number of chunks deleted.
        """
        endpoint = self._settings.databricks_vector_search_endpoint
        index_name = self._index_names[index_key]
        index = self._index_cache.get(index_key)
        if index is None:
            index = self._client.get_index(endpoint_name=endpoint, index_name=index_name)
            self._index_cache[index_key] = index

        total = 0
        already: set[str] = set()
        for _round in range(max_rounds):
            chunks = self.similarity_search(
                index_key=index_key,
                query="_",
                scope_id=None,
                top_k=batch,
                filters={"auditor_folder": auditor_folder},
            )
            ids = [
                c.chunk_id for c in chunks
                if (c.auditor_folder or "") == auditor_folder and c.chunk_id not in already
            ]
            if not ids:
                break
            index.delete(primary_keys=ids)
            already.update(ids)
            total += len(ids)
            logger.info(
                "[VectorIndex] Deleted %d chunks (round %d) | index=%s | auditor_folder=%s",
                len(ids), _round + 1, index_name, auditor_folder,
            )
            time.sleep(2)  # allow deletion to propagate before the next scan
        logger.info(
            "[VectorIndex] Delete-by-folder complete | index=%s | auditor_folder=%s | total=%d",
            index_name, auditor_folder, total,
        )
        return total

    def delete_by_document_name(
        self, index_key: str, document_name: str, auditor_folder: str | None = None,
        batch: int = 1000, max_rounds: int = 500,
    ) -> int:
        """Delete ALL chunks of a given source file (document_name) from the index. Used by the admin
        file APIs to keep a global-policy index in sync when a file is deleted/replaced, and by KB
        re-ingest-on-change. Loops until no matching chunks remain; returns the number deleted.

        `auditor_folder` (optional) scopes the delete to a single auditor — REQUIRED on per-auditor
        shared indexes so re-ingesting one auditor's edited file never removes another auditor's
        same-named file. Omit it for global (single-tenant) indexes to delete the file everywhere."""
        endpoint = self._settings.databricks_vector_search_endpoint
        index_name = self._index_names[index_key]
        index = self._index_cache.get(index_key)
        if index is None:
            index = self._client.get_index(endpoint_name=endpoint, index_name=index_name)
            self._index_cache[index_key] = index

        filters: dict[str, Any] = {"document_name": document_name}
        if auditor_folder:
            filters["auditor_folder"] = auditor_folder

        total = 0
        already: set[str] = set()
        for _round in range(max_rounds):
            chunks = self.similarity_search(
                index_key=index_key,
                query="_",
                scope_id=None,
                top_k=batch,
                filters=filters,
            )
            ids = [
                c.chunk_id for c in chunks
                if (c.document_name or "") == document_name
                and (auditor_folder is None or (c.auditor_folder or "") == auditor_folder)
                and c.chunk_id not in already
            ]
            if not ids:
                break
            index.delete(primary_keys=ids)
            already.update(ids)
            total += len(ids)
            logger.info(
                "[VectorIndex] Deleted %d chunks (round %d) | index=%s | document=%s | auditor=%s",
                len(ids), _round + 1, index_name, document_name, auditor_folder or "(all)",
            )
            time.sleep(2)  # allow deletion to propagate before the next scan
        logger.info(
            "[VectorIndex] Delete-by-document complete | index=%s | document=%s | auditor=%s | total=%d",
            index_name, document_name, auditor_folder or "(all)", total,
        )
        return total

    def wait_until_ready(self, index_key: str, timeout_s: int = 300) -> None:
        index = self._index_cache.get(index_key)
        if index is None:
            return
        epsilon = [_PROBE_EPSILON] * self._settings.embedding_dimensions
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                index.similarity_search(
                    columns=["chunk_id"], query_vector=epsilon, num_results=1
                )
                logger.info("[VectorIndex] Query-ready | index=%s", self._index_names[index_key])
                return
            except Exception as exc:
                exc_str = str(exc).lower()
                if any(phrase in exc_str for phrase in ("not ready", "pending deletion", "does not exist")):
                    logger.info(
                        "[VectorIndex] Waiting for index to become query-ready | index=%s | reason=%s",
                        index_key, exc,
                    )
                    time.sleep(10)
                    continue
                logger.info(
                    "[VectorIndex] Ready (probe returned non-readiness result) | index=%s | %s",
                    index_key, exc,
                )
                return
        logger.warning(
            "[VectorIndex] Not confirmed query-ready after %ds — proceeding | index=%s",
            timeout_s, index_key,
        )

    def is_populated(self, index_key: str, scope_id: str) -> bool:
        try:
            index = self._client.get_index(
                endpoint_name=self._settings.databricks_vector_search_endpoint,
                index_name=self._index_names[index_key],
            )
            epsilon_vector = [_PROBE_EPSILON] * self._settings.embedding_dimensions
            results = index.similarity_search(
                columns=["chunk_id"],
                query_vector=epsilon_vector,
                filters={"scope_id": scope_id},
                num_results=1,
            )
            rows = results.get("result", {}).get("data_array", [])
            populated = len(rows) > 0
            logger.info(
                "[VectorIndex] is_populated | index=%s | scope=%s | result=%s | rows=%d",
                index_key, scope_id, populated, len(rows),
            )
            return populated
        except Exception as exc:
            logger.warning(
                "[VectorIndex] is_populated check failed — treating as empty | index=%s | error=%s",
                index_key, exc,
            )
            return False

    def is_populated_any(self, index_key: str) -> bool:
        """Check if an index has any data — no scope_id filter applied."""
        try:
            index = self._client.get_index(
                endpoint_name=self._settings.databricks_vector_search_endpoint,
                index_name=self._index_names[index_key],
            )
            epsilon_vector = [_PROBE_EPSILON] * self._settings.embedding_dimensions
            results = index.similarity_search(
                columns=["chunk_id"],
                query_vector=epsilon_vector,
                num_results=1,
            )
            rows = results.get("result", {}).get("data_array", [])
            populated = len(rows) > 0
            logger.info(
                "[VectorIndex] is_populated_any | index=%s | result=%s | rows=%d",
                index_key, populated, len(rows),
            )
            return populated
        except Exception as exc:
            logger.warning(
                "[VectorIndex] is_populated_any check failed — treating as empty | index=%s | error=%s",
                index_key, exc,
            )
            return False

    def list_document_names(self, index_key: str, auditor_folder: str) -> set[str]:
        """Distinct document_name values held for an auditor — used to verify that a cited supporting
        file actually exists before it lands in the output. Empty set on any error (caller fails open)."""
        try:
            index = self._client.get_index(
                endpoint_name=self._settings.databricks_vector_search_endpoint,
                index_name=self._index_names[index_key],
            )
            epsilon_vector = [_PROBE_EPSILON] * self._settings.embedding_dimensions
            results = index.similarity_search(
                columns=["document_name"],
                query_vector=epsilon_vector,
                filters={"auditor_folder": auditor_folder},
                num_results=10000,  # index hard cap; projected column keeps the payload small
            )
            rows = results.get("result", {}).get("data_array", []) or []
            names = {str(r[0]).strip() for r in rows if r and r[0]}
            logger.info("[VectorIndex] list_document_names | index=%s | auditor=%s | files=%d",
                        index_key, auditor_folder, len(names))
            return names
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VectorIndex] list_document_names failed — returning empty | index=%s | "
                           "auditor=%s | error=%s", index_key, auditor_folder, exc)
            return set()

    def is_populated_for_auditor(self, index_key: str, auditor_folder: str) -> bool:
        """Check whether an index already holds data for a specific auditor_folder."""
        try:
            index = self._client.get_index(
                endpoint_name=self._settings.databricks_vector_search_endpoint,
                index_name=self._index_names[index_key],
            )
            epsilon_vector = [_PROBE_EPSILON] * self._settings.embedding_dimensions
            results = index.similarity_search(
                columns=["chunk_id"],
                query_vector=epsilon_vector,
                filters={"auditor_folder": auditor_folder},
                num_results=1,
            )
            rows = results.get("result", {}).get("data_array", [])
            return len(rows) > 0
        except Exception as exc:
            logger.warning(
                "[VectorIndex] is_populated_for_auditor failed — treating as empty | "
                "index=%s | auditor=%s | error=%s",
                index_key, auditor_folder, exc,
            )
            return False

    def fetch_document_hash(self, index_key: str, auditor_folder: str, document_name: str) -> str:
        """Return the stored content_hash for one indexed file (auditor_folder + document_name).

        Empty string when the file is not yet indexed or was ingested before hashes existed — the KB
        caller treats "" as new/changed and re-ingests, which also backfills the hash. Best-effort:
        any failure returns "" (→ re-ingest), so a lookup error never hides a real update."""
        try:
            chunks = self.similarity_search(
                index_key=index_key, query="_", scope_id=None, top_k=1,
                filters={"auditor_folder": auditor_folder, "document_name": document_name},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[VectorIndex] fetch_document_hash failed — treating file as changed | "
                "index=%s | doc=%s | error=%s", index_key, document_name, exc,
            )
            return ""
        for c in chunks:
            if (c.document_name or "") == document_name:
                return str((c.metadata or {}).get("content_hash") or "")
        return ""

    def upsert_chunks(self, index_key: str, chunks: list[DocumentChunk], batch_size: int = 50) -> None:
        """Embed and upsert DocumentChunks in parallel batches of 50, logging progress after each."""
        if index_key not in self._index_names:
            raise ValueError(f"Unknown index key: {index_key!r}. Must be one of {list(self._index_names)}")

        index = self._index_cache.get(index_key)
        if index is None:
            raise RuntimeError(
                f"Index '{index_key}' not in cache — ensure_index() must be called first"
            )

        total = len(chunks)
        index_name = self._index_names[index_key]
        batches = [chunks[i : i + batch_size] for i in range(0, total, batch_size)]
        logger.info(
            "[VectorIndex] Upserting %d chunks | batches=%d | workers=4 | index=%s",
            total, len(batches), index_name,
        )

        upserted = 0
        _lock = threading.Lock()

        def _upsert_batch(batch: list[DocumentChunk]) -> int:
            texts = [c.content for c in batch]
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    embeddings = self._embeddings.embed_documents(texts)
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "[VectorIndex] embed_documents failed (attempt %d/3) | batch_size=%d | error=%s",
                        attempt + 1, len(batch), exc,
                    )
                    time.sleep(5)
            else:
                raise RuntimeError("embed_documents failed after 3 attempts") from last_exc

            records = []
            for chunk, embedding in zip(batch, embeddings):
                meta = chunk.metadata
                records.append({
                    "chunk_id":       chunk.chunk_id,
                    "document_name":  chunk.document_name,
                    "source":         chunk.source.value,
                    "content":        chunk.content,
                    "token_count":    chunk.token_count,
                    "scope_id":       chunk.scope_id,
                    "auditor_folder": chunk.auditor_folder or "",
                    "embedding":      list(embedding),
                    "document_type":  _to_str(meta.get("document_type")),
                    "effective_date": _to_str(meta.get("effective_date")),
                    "country":        _to_str(meta.get("country")),
                    "policy_number":  _to_str(meta.get("policy_number")),
                    "policy_domain":  _to_str(meta.get("policy_domain")),
                    "section_no":     _to_str(meta.get("section_no")),
                    "metadata_json":  json.dumps({
                        k: _json_safe(meta.get(k))
                        for k in (
                            # section + page stored under any variant key the chunker used,
                            # plus canonical page_number/section normalised from those variants
                            "section_title", "section", "section_no", "section_number",
                            "section_name", "section_heading",
                            "page_number", "page", "page_no", "page_num", "page_label", "pageno",
                            "version", "language",
                            "policy_name", "policy_owner", "index_name",
                            "file_format", "ingestion_timestamp", "parent_email",
                            # per-file content fingerprint — enables KB re-ingest-on-change detection
                            "content_hash",
                        )
                        if meta.get(k) is not None
                    } | {
                        # canonical keys so reads never miss them regardless of source spelling
                        kc: v for kc, v in (
                            ("page_number", _first_nonempty(meta, _PAGE_KEYS)),
                            ("section",     _first_nonempty(meta, _SECTION_KEYS)),
                        ) if v
                    }),
                })

            # A newly provisioned index can briefly reject writes with "index is not ready";
            # retry with backoff so first-run ingestion chunks are not silently dropped.
            response = None
            for _rt in range(6):
                try:
                    response = index.upsert(records)
                    break
                except Exception as exc:  # noqa: BLE001
                    if "not ready" in str(exc).lower() and _rt < 5:
                        logger.info(
                            "[VectorIndex] Index not ready on upsert — retrying in 10s | "
                            "index=%s | attempt=%d/6", index_name, _rt + 1,
                        )
                        time.sleep(10)
                        continue
                    raise
            failed_keys = (
                response.get("result", {}).get("failed_primary_keys", [])
                if isinstance(response, dict) else []
            )
            if failed_keys:
                logger.error(
                    "[VectorIndex] Upsert partial failure | index=%s | failed=%d/%d | sample_ids=%s",
                    index_name, len(failed_keys), len(records), failed_keys[:3],
                )
                raise RuntimeError(
                    f"Upsert failed for {len(failed_keys)}/{len(records)} chunks in {index_name}"
                )
            return len(batch)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_upsert_batch, b): b for b in batches}
            for fut in as_completed(futures):
                n = fut.result()
                with _lock:
                    upserted += n
                    logger.info(
                        "[VectorIndex] Upserted %d/%d chunks | index=%s",
                        upserted, total, index_name,
                    )

        logger.info("[VectorIndex] Upsert complete | total=%d | index=%s", total, index_name)

    def _apply_page_offset(self, page_value: str) -> str:
        """Shift a cited page number by the configured display offset (kept >= 1)."""
        offset = self._settings.page_number_display_offset
        if not offset or not page_value:
            return page_value
        try:
            adjusted = int(str(page_value).strip()) + offset
        except (TypeError, ValueError):
            return page_value
        return str(adjusted) if adjusted >= 1 else page_value

    def similarity_search(
        self,
        index_key: str,
        query: str,
        scope_id: str | None = None,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        """Embed query and return top_k DocumentChunks from the specified index, filtered by scope_id."""
        query_embedding = self._embeddings.embed_query(query)

        index = self._index_cache.get(index_key) or self._client.get_index(
            endpoint_name=self._settings.databricks_vector_search_endpoint,
            index_name=self._index_names[index_key],
        )

        combined_filters: dict[str, Any] = {"scope_id": scope_id} if scope_id else {}
        if filters:
            combined_filters.update(filters)

        _COLUMNS = [
            "chunk_id", "document_name", "source", "content", "token_count", "scope_id",
            "document_type", "effective_date", "country", "policy_number",
            "policy_domain", "section_no", "metadata_json", "auditor_folder",
        ]
        results = index.similarity_search(
            columns=_COLUMNS,
            query_vector=query_embedding,
            filters=combined_filters,
            num_results=top_k,
        )

        chunks: list[DocumentChunk] = []
        for row in results.get("result", {}).get("data_array", []):
            display_meta = json.loads(row[12] or "{}") if len(row) > 12 and row[12] else {}
            metadata = {
                "document_type":  row[6]  if len(row) > 6  else "",
                "effective_date": row[7]  if len(row) > 7  else "",
                "country":        row[8]  if len(row) > 8  else "",
                "policy_number":  row[9]  if len(row) > 9  else "",
                "policy_domain":  row[10] if len(row) > 10 else "",
                "section_no":     row[11] if len(row) > 11 else "",
                **display_meta,
            }
            # Canonicalise page/section so downstream citation always finds them regardless of
            # which key spelling the source used in metadata_json.
            metadata["page_number"] = self._apply_page_offset(_first_nonempty(metadata, _PAGE_KEYS))
            metadata["section"]     = _first_nonempty(metadata, _SECTION_KEYS)
            auditor_folder = (row[13] or None) if len(row) > 13 else None
            chunk = DocumentChunk(
                chunk_id=row[0],
                document_name=row[1],
                source=DocumentSource(row[2]),
                content=row[3],
                token_count=row[4],
                scope_id=row[5],
                auditor_folder=auditor_folder,
                metadata=metadata,
                similarity_score=row[-1] if len(row) > 14 else None,
            )
            chunks.append(chunk)

        return chunks

    def search_questionnaire(
        self, query: str, auditor_id: str | None = None, top_k: int = 5
    ) -> list[DocumentChunk]:
        """Query the SHARED questionnaire index (Scoping-Deck schema) and return DocumentChunks.

        Separate from similarity_search because the questionnaire index has its own columns. Filters to
        the auditor via `auditor_id` (the questionnaire schema's own field). Returns [] on any failure."""
        index = self._index_cache.get("questionnaire") or self._client.get_index(
            endpoint_name=self._settings.databricks_vector_search_endpoint,
            index_name=self._index_names["questionnaire"],
        )
        self._index_cache.setdefault("questionnaire", index)
        try:
            query_embedding = self._embeddings.embed_query(query)
            results = index.similarity_search(
                columns=_QUESTIONNAIRE_COLUMNS,
                query_vector=query_embedding,
                filters={"auditor_id": auditor_id} if auditor_id else None,
                num_results=top_k,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VectorIndex] questionnaire search failed — returning empty | %s", exc)
            return []

        chunks: list[DocumentChunk] = []
        for row in results.get("result", {}).get("data_array", []):
            data = dict(zip(_QUESTIONNAIRE_COLUMNS, row))
            content = data.get("content") or (
                f"Q: {data.get('question_text', '')}\nResponse: {data.get('response_text', '')}"
            )
            chunks.append(DocumentChunk(
                chunk_id=data.get("chunk_id", "") or "q",
                document_name=data.get("source_table", "") or "questionnaire",
                source=DocumentSource.QUESTIONNAIRE,
                content=content,
                token_count=max(1, len(content.split())),
                scope_id=data.get("scope_id", "") or "",
                auditor_folder=data.get("auditor_id") or None,
                # Keep the question id + source table so callers can cite the exact file + question.
                metadata={
                    "question_id": data.get("question_id", "") or "",
                    "source_table": data.get("source_table", "") or "",
                },
                similarity_score=row[-1] if len(row) > len(_QUESTIONNAIRE_COLUMNS) else None,
            ))
        return chunks

    def ensure_questionnaire_index(self) -> None:
        """Create the SHARED questionnaire index (its own schema) if absent; wait until query-ready.

        The main app owns creation/population; the Scoping Deck consumes it. Uses the questionnaire
        schema (not the standard chunk schema), so it cannot go through the generic ensure_index."""
        endpoint = self._settings.databricks_vector_search_endpoint
        index_name = self._index_names["questionnaire"]
        try:
            self._index_cache["questionnaire"] = self._client.get_index(
                endpoint_name=endpoint, index_name=index_name
            )
            logger.info("[VectorIndex] Already exists | index=%s", index_name)
            return
        except Exception:
            pass

        logger.info("[VectorIndex] Creating questionnaire index | index=%s", index_name)
        self._client.create_direct_access_index(
            endpoint_name=endpoint,
            index_name=index_name,
            primary_key="chunk_id",
            embedding_dimension=self._settings.embedding_dimensions,
            embedding_vector_column="embedding",
            schema=_QUESTIONNAIRE_SCHEMA,
        )
        for attempt in range(_INDEX_POLL_ATTEMPTS):
            try:
                self._index_cache["questionnaire"] = self._client.get_index(
                    endpoint_name=endpoint, index_name=index_name
                )
                break
            except Exception:
                logger.info("[VectorIndex] Waiting for questionnaire index metadata | attempt=%d/24", attempt + 1)
                time.sleep(5)
        else:
            raise RuntimeError(f"Index {index_name} not available 120s after creation")
        self.wait_until_ready("questionnaire", timeout_s=600)
        logger.info("[VectorIndex] questionnaire index write/query ready | index=%s", index_name)

    def is_questionnaire_populated(self, auditor_id: str) -> bool:
        """True when the questionnaire index already holds at least one row for this auditor."""
        try:
            index = self._index_cache.get("questionnaire") or self._client.get_index(
                endpoint_name=self._settings.databricks_vector_search_endpoint,
                index_name=self._index_names["questionnaire"],
            )
            results = index.similarity_search(
                columns=["chunk_id"],
                query_vector=[_PROBE_EPSILON] * self._settings.embedding_dimensions,
                filters={"auditor_id": auditor_id} if auditor_id else None,
                num_results=1,
            )
            return len(results.get("result", {}).get("data_array", [])) > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VectorIndex] questionnaire is_populated check failed — treating as empty | %s", exc)
            return False

    def delete_questionnaire_by_auditor(
        self, auditor_id: str, batch: int = 1000, max_rounds: int = 500
    ) -> int:
        """Delete ALL questionnaire-index rows for one auditor (its own schema filters on `auditor_id`,
        not `auditor_folder`, so delete_by_auditor_folder does NOT apply here). Repeatedly fetches up to
        `batch` rows for the auditor and deletes them by primary key, looping until none remain.
        Self-terminating, idempotent; returns the number of rows deleted."""
        endpoint = self._settings.databricks_vector_search_endpoint
        index_name = self._index_names["questionnaire"]
        index = self._index_cache.get("questionnaire") or self._client.get_index(
            endpoint_name=endpoint, index_name=index_name
        )
        self._index_cache.setdefault("questionnaire", index)

        epsilon = [_PROBE_EPSILON] * self._settings.embedding_dimensions
        total = 0
        already: set[str] = set()
        for _round in range(max_rounds):
            results = index.similarity_search(
                columns=["chunk_id", "auditor_id"],
                query_vector=epsilon,
                filters={"auditor_id": auditor_id} if auditor_id else None,
                num_results=batch,
            )
            rows = results.get("result", {}).get("data_array", [])
            ids = [
                r[0] for r in rows
                if r and r[0] and r[0] not in already and (not auditor_id or (len(r) > 1 and r[1] == auditor_id))
            ]
            if not ids:
                break
            index.delete(primary_keys=ids)
            already.update(ids)
            total += len(ids)
            logger.info(
                "[VectorIndex] Deleted %d questionnaire row(s) (round %d) | index=%s | auditor_id=%s",
                len(ids), _round + 1, index_name, auditor_id,
            )
            time.sleep(2)  # allow deletion to propagate before the next scan
        logger.info(
            "[VectorIndex] Delete-questionnaire-by-auditor complete | index=%s | auditor_id=%s | total=%d",
            index_name, auditor_id, total,
        )
        return total

    def upsert_questionnaire(self, records: list[dict], batch_size: int = 50) -> None:
        """Embed each record's `content` and upsert to the questionnaire index (its own schema)."""
        if not records:
            return
        index = self._index_cache.get("questionnaire") or self._client.get_index(
            endpoint_name=self._settings.databricks_vector_search_endpoint,
            index_name=self._index_names["questionnaire"],
        )
        self._index_cache.setdefault("questionnaire", index)
        total = len(records)
        for start in range(0, total, batch_size):
            batch = records[start:start + batch_size]
            embeddings = self._embeddings.embed_documents([r.get("content", "") for r in batch])
            rows = []
            for rec, emb in zip(batch, embeddings):
                row = {col: str(rec.get(col, "") or "") for col in _QUESTIONNAIRE_COLUMNS}
                row["embedding"] = list(emb)
                rows.append(row)
            index.upsert(rows)
            logger.info("[VectorIndex] questionnaire upserted %d/%d", min(start + batch_size, total), total)
        logger.info("[VectorIndex] questionnaire upsert complete | total=%d | index=%s",
                    total, self._index_names["questionnaire"])
