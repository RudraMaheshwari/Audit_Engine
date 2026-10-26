"""Admin file management for the three GLOBAL knowledge sources, keeping each vector index in sync.

Resources:
  - global-policies        -> HCBI volume          -> `hcbi` index        (generic ingest)
  - global-scope-policies  -> scope-area policies   -> `policy` index      (generic ingest)
  - fcpa-guide             -> CPR guide .xlsx       -> `scope_policy` index (CPR extractor; single file)

CRUD, each keeping the index consistent:
  list    — files in the volume
  download— file bytes
  upload  — write to the volume AND (re)ingest into the index (replaces an existing file's chunks)
  delete  — remove from the volume AND delete that file's chunks from the index

Dependencies (Databricks SP auth, embeddings, OCR) are built per operation — admin actions are
low-frequency, so a fresh, isolated set per call keeps this simple and thread-safe.
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import AppError, NotFoundError
from app.schemas.admin import DeleteResult, FileInfo, FileListResponse, IngestResult
from src.config.constants import HCBI_SCOPE_ID
from src.config.databricks_auth import build_config
from src.config.settings import get_settings
from src.models.schemas import DocumentSource
from src.schemas.state import DocumentType
from src.utils.logger import get_logger

logger = get_logger("app.admin_files")

_GENERIC = "generic"
_CPR = "cpr"


@dataclass(frozen=True)
class _Resource:
    slug: str
    index_key: str
    path_attr: str          # settings attribute: a FOLDER (generic) or a FILE path (fcpa)
    style: str              # _GENERIC | _CPR
    single_file: bool       # fcpa-guide is one fixed .xlsx
    doc_type: DocumentType | None = None
    doc_source: DocumentSource | None = None
    hcbi_scope: bool = False  # generic: use HCBI_SCOPE_ID vs settings.current_scope_id


_RESOURCES: dict[str, _Resource] = {
    "global-policies": _Resource(
        slug="global-policies", index_key="hcbi", path_attr="hcbi_volume_path",
        style=_GENERIC, single_file=False,
        doc_type=DocumentType.GLOBAL_POLICY, doc_source=DocumentSource.HCBI, hcbi_scope=True,
    ),
    "global-scope-policies": _Resource(
        slug="global-scope-policies", index_key="policy", path_attr="policy_volume_path",
        style=_GENERIC, single_file=False,
        doc_type=DocumentType.TPI_POLICY, doc_source=DocumentSource.POLICY,
    ),
    "fcpa-guide": _Resource(
        slug="fcpa-guide", index_key="scope_policy", path_attr="scope_area_policy_file_path",
        style=_CPR, single_file=True,
    ),
}


def resource_slugs() -> list[str]:
    return list(_RESOURCES)


def _resource(slug: str) -> _Resource:
    res = _RESOURCES.get(slug)
    if res is None:
        raise NotFoundError(f"Unknown admin resource '{slug}'.", details={"valid": resource_slugs()})
    return res


class AdminFileService:
    """CRUD over a global knowledge source's files, with index synchronisation."""

    def __init__(self) -> None:
        self._settings = get_settings()

    # ── Databricks file (Volume) access via the service principal ─────────────
    def _ws(self):
        from databricks.sdk import WorkspaceClient
        return WorkspaceClient(config=build_config(self._settings))

    def _base_path(self, res: _Resource) -> str:
        return str(getattr(self._settings, res.path_attr, "") or "").rstrip("/")

    def _folder(self, res: _Resource) -> str:
        """The directory to list/write files in (parent dir for the single-file FCPA guide)."""
        base = self._base_path(res)
        return base.rsplit("/", 1)[0] if res.single_file else base

    def _file_path(self, res: _Resource, name: str) -> str:
        if res.single_file:
            return self._base_path(res)  # fixed path; name is informational
        return f"{self._folder(res)}/{name}"

    # ── List ──────────────────────────────────────────────────────────────────
    def list_files(self, slug: str) -> FileListResponse:
        res = _resource(slug)
        ws = self._ws()
        files: list[FileInfo] = []
        try:
            if res.single_file:
                path = self._base_path(res)
                try:
                    meta = ws.files.get_metadata(path)
                    files.append(FileInfo(name=Path(path).name, path=path,
                                          size_bytes=getattr(meta, "content_length", None)))
                except Exception:
                    pass  # file not present yet
            else:
                for e in ws.files.list_directory_contents(self._folder(res)):
                    if getattr(e, "is_directory", False):
                        continue
                    files.append(FileInfo(
                        name=Path(e.path).name, path=e.path,
                        size_bytes=getattr(e, "file_size", None),
                        modified=str(getattr(e, "last_modified", "") or "") or None,
                    ))
        except Exception as exc:  # noqa: BLE001
            raise AppError(f"Could not list files for '{slug}': {exc}") from exc
        return FileListResponse(resource=slug, index_key=res.index_key, count=len(files), files=files)

    # ── Download ───────────────────────────────────────────────────────────────
    def download_file(self, slug: str, name: str) -> tuple[bytes, str]:
        res = _resource(slug)
        path = self._file_path(res, name)
        try:
            data = self._ws().files.download(path).contents.read()
        except Exception as exc:  # noqa: BLE001
            raise NotFoundError(f"File not found: {name}", details={"path": path}) from exc
        return data, Path(path).name

    # ── Upload (write + ingest) ────────────────────────────────────────────────
    def upload_file(self, slug: str, name: str, content: bytes) -> IngestResult:
        res = _resource(slug)
        name = Path(res.single_file and self._base_path(res) or name).name
        dest = self._file_path(res, name)

        # Write to the Volume (overwrite = update).
        try:
            self._ws().files.upload(dest, io.BytesIO(content), overwrite=True)
        except Exception as exc:  # noqa: BLE001
            raise AppError(f"Upload to volume failed: {exc}") from exc

        if res.style == _CPR:
            removed, chunks = self._reingest_cpr(res, content)
        else:
            removed, chunks = self._reingest_generic(res, name, content)

        status = "replaced" if removed else "ingested"
        logger.info("[Admin] %s %s | file=%s | chunks=%d (removed %d)", status, slug, name, chunks, removed)
        return IngestResult(resource=slug, file=name, status=status,
                            chunks_indexed=chunks, chunks_removed=removed, path=dest)

    # ── Delete (remove + de-index) ──────────────────────────────────────────────
    def delete_file(self, slug: str, name: str) -> DeleteResult:
        res = _resource(slug)
        path = self._file_path(res, name)
        name = Path(path).name

        deleted = True
        try:
            self._ws().files.delete(path)
        except Exception as exc:  # noqa: BLE001
            deleted = False
            logger.warning("[Admin] Volume delete failed | %s | %s", path, exc)

        mgr = self._index_manager()
        if res.style == _CPR:
            # Single-file index: clear it entirely.
            removed = self._clear_index(mgr, res.index_key)
        else:
            removed = mgr.delete_by_document_name(res.index_key, name)
        logger.info("[Admin] deleted %s | file=%s | chunks_removed=%d", res.slug, name, removed)
        return DeleteResult(resource=slug, file=name, deleted_from_volume=deleted, chunks_removed=removed)

    # ── Ingestion internals ─────────────────────────────────────────────────────
    def _index_manager(self):
        from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
        from src.vector_store.index_manager import VectorIndexManager
        return VectorIndexManager(self._settings, AzureOpenAIEmbeddingProvider(self._settings))

    def _reingest_generic(self, res: _Resource, name: str, content: bytes) -> tuple[int, int]:
        """Replace one file's chunks in a generic index: clear old (by name) → extract → chunk → upsert."""
        from src.pipeline.runner import _build_ocr_provider  # OCR provider selection (DRY)
        from src.ingestion.chunker import Chunker
        from src.ingestion.loader import DocumentLoader

        mgr = self._index_manager()
        mgr.ensure_index(res.index_key)
        removed = mgr.delete_by_document_name(res.index_key, name)

        scope_id = HCBI_SCOPE_ID if res.hcbi_scope else self._settings.current_scope_id
        loader = DocumentLoader(_build_ocr_provider(self._settings))
        chunker = Chunker(chunk_size=self._settings.chunk_size, chunk_overlap=self._settings.chunk_overlap)

        with tempfile.TemporaryDirectory(prefix="admin_ingest_") as tmp:
            local = Path(tmp) / name
            local.write_bytes(content)
            doc = loader.load_file(local, scope_id=scope_id, document_type=res.doc_type)
            chunks = chunker.chunk(doc, res.doc_source)
        if not chunks:
            return removed, 0
        mgr.upsert_chunks(res.index_key, chunks)
        mgr.wait_until_ready(res.index_key)
        return removed, len(chunks)

    def _reingest_cpr(self, res: _Resource, content: bytes) -> tuple[int, int]:
        """FCPA guide is one workbook: rebuild the scope_policy index from the new file."""
        from src.ingestion.extractors.cpr_scope_extractor import CPRScopeExtractor

        mgr = self._index_manager()
        removed = self._clear_index(mgr, res.index_key)
        with tempfile.TemporaryDirectory(prefix="admin_cpr_") as tmp:
            local = Path(tmp) / Path(self._base_path(res)).name
            local.write_bytes(content)
            chunks = CPRScopeExtractor().extract(local)
        if not chunks:
            return removed, 0
        mgr.upsert_chunks(res.index_key, chunks)
        mgr.wait_until_ready(res.index_key)
        return removed, len(chunks)

    @staticmethod
    def _clear_index(mgr, index_key: str) -> int:
        """Reset an index to empty (used for the single-file FCPA guide). Best-effort count."""
        try:
            mgr.delete_index(index_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Admin] delete_index failed | %s | %s", index_key, exc)
        mgr.ensure_index(index_key)
        return 0
