"""Per-auditor supporting-document ingestion for UI-uploaded files.

Takes an `auditor_id` and an uploaded file, then:
  1. writes the file into that auditor's supporting-docs folder on the Databricks volume
     (<supporting-docs base volume>/<auditor_id>/<name>) so a later full run sees it too, and
  2. ingests it into the shared `supporting` vector index, tagged with `auditor_folder=auditor_id`,
     replacing this auditor's chunks for a same-named file (an update) — never another auditor's.

Mirrors the pipeline's own supporting-docs ingest (scope_id="supporting", DocumentSource.SUPPORTING).
Dependencies (Databricks SP auth, embeddings, OCR, translation) are built per call — uploads are
low-frequency, so a fresh, isolated set per request keeps this simple and thread-safe.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from pathlib import Path

from app.core.exceptions import AppError
from app.schemas.admin import SupportingDocIngestResult
from src.config.databricks_auth import build_config
from src.config.settings import get_settings
from src.models.schemas import DocumentSource
from src.schemas.state import DocumentType
from src.utils.logger import get_logger

logger = get_logger("app.supporting_ingest")

_INDEX_KEY = "supporting"
_SCOPE_ID = "supporting"


class SupportingDocIngestService:
    """Write + ingest a UI-uploaded supporting document into one auditor's supporting-docs index."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def _ws(self):
        from databricks.sdk import WorkspaceClient
        return WorkspaceClient(config=build_config(self._settings))

    def _index_manager(self):
        from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
        from src.vector_store.index_manager import VectorIndexManager
        return VectorIndexManager(self._settings, AzureOpenAIEmbeddingProvider(self._settings))

    def _auditor_folder(self, auditor_id: str) -> str:
        base = str(self._settings.auditors_supporting_docs_volume_path or "").rstrip("/")
        return f"{base}/{auditor_id}"

    def upload_file(self, auditor_id: str, name: str, content: bytes) -> SupportingDocIngestResult:
        auditor_id = (auditor_id or "").strip()
        if not auditor_id:
            raise AppError("auditor_id is required.")
        name = Path(name or "upload").name
        if not content:
            raise AppError("Uploaded file is empty.")

        folder = self._auditor_folder(auditor_id)
        dest = f"{folder}/{name}"

        # 1) Write into the auditor's supporting-docs folder (create it if this is the first upload).
        #    Overwrite = update the file in place.
        ws = self._ws()
        try:
            ws.files.create_directory(folder)
        except Exception:  # noqa: BLE001 — already exists / not needed
            pass
        try:
            ws.files.upload(dest, io.BytesIO(content), overwrite=True)
        except Exception as exc:  # noqa: BLE001
            raise AppError(f"Upload to volume failed: {exc}") from exc

        # 2) Ingest into the supporting index for THIS auditor (replace same-named file, scoped to auditor).
        removed, indexed = self._reingest(auditor_id, name, content)

        status = "replaced" if removed else "ingested"
        logger.info(
            "[SupportingDoc] %s | auditor=%s | file=%s | chunks=%d (removed %d) | path=%s",
            status, auditor_id, name, indexed, removed, dest,
        )
        return SupportingDocIngestResult(
            auditor_id=auditor_id, file=name, status=status,
            chunks_indexed=indexed, chunks_removed=removed, path=dest,
        )

    def _reingest(self, auditor_id: str, name: str, content: bytes) -> tuple[int, int]:
        """Clear this auditor's old chunks for the file, extract → chunk → tag → upsert the new bytes."""
        from src.ingestion.chunker import Chunker
        from src.ingestion.loader import DocumentLoader
        from src.pipeline.runner import _build_ocr_provider
        from src.services.translation import TranslationService

        mgr = self._index_manager()
        mgr.ensure_index(_INDEX_KEY)
        # Scoped to this auditor so updating one auditor's file never removes another's same-named file.
        removed = mgr.delete_by_document_name(_INDEX_KEY, name, auditor_folder=auditor_id)

        loader = DocumentLoader(
            _build_ocr_provider(self._settings),
            translator=TranslationService(self._settings),
            settings=self._settings,
        )
        chunker = Chunker(chunk_size=self._settings.chunk_size, chunk_overlap=self._settings.chunk_overlap)

        with tempfile.TemporaryDirectory(prefix="supporting_ingest_") as tmp:
            local = Path(tmp) / name
            local.write_bytes(content)
            doc = loader.load_file(local, scope_id=_SCOPE_ID, document_type=DocumentType.SUPPORTING_DOC)
            chunks = chunker.chunk(doc, DocumentSource.SUPPORTING)

        if not chunks:
            logger.warning("[SupportingDoc] No chunks produced | auditor=%s | file=%s", auditor_id, name)
            return removed, 0

        # Tag every chunk with the auditor folder (retrieval filters on it) + a content fingerprint.
        content_hash = hashlib.sha256(content).hexdigest()
        chunks = [
            c.model_copy(update={
                "auditor_folder": auditor_id,
                "metadata": {**(c.metadata or {}), "content_hash": content_hash},
            })
            for c in chunks
        ]
        mgr.upsert_chunks(_INDEX_KEY, chunks)
        mgr.wait_until_ready(_INDEX_KEY)
        return removed, len(chunks)
