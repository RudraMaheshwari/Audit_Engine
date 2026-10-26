"""Resume supporting-docs ingestion (pure ingest, no deletes, NO size skipping).

Resumes the `supporting` index ingestion from a given file in the first folder, then ingests
the remaining folders in full. Upserts are idempotent by chunk_id, so re-running is safe.

Default plan:
  - Naeem_1 : resume from RESUME_FROM (default 'Q71- IMS_April23_Sep24.xlsx') onward
  - Naeem_2, Naeem_3, Sam_1 : ingest all files

Big data-dump spreadsheets ARE ingested (no skipping). To survive them, concurrency defaults to
2 workers and the per-file timeout to 1800s — tune with WORKERS / FILE_TIMEOUT if needed.

Run:
    python scripts/reingest_supporting_auditors.py

Env overrides:
    set INGEST_FOLDERS=Naeem_1,Naeem_2,Naeem_3,Sam_1
    set RESUME_FROM=Q71- IMS_April23_Sep24.xlsx      (applies to the FIRST folder; blank = whole folder)
    set WORKERS=2
    set FILE_TIMEOUT=1800
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.ingestion.chunker import Chunker
from src.ingestion.loader import DocumentLoader
from src.ingestion.volume_downloader import DatabricksVolumeDownloader
from src.models.schemas import DocumentSource
from src.pipeline.runner import _build_ocr_provider, _process_and_upsert_file
from src.schemas.state import DocumentType
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger("reingest_supporting_auditors")

DEFAULT_INGEST_FOLDERS = ["Naeem_1", "Naeem_2", "Naeem_3", "Sam_1"]
DEFAULT_RESUME_FROM = "Q71- IMS_April23_Sep24.xlsx"

_WORKERS = int(os.environ.get("WORKERS", "2"))           # lower concurrency so giant files don't stack in memory
_FILE_TIMEOUT = int(os.environ.get("FILE_TIMEOUT", "1800"))  # 30 min/file — giant sheets need time to embed+upsert


def _auditor_folder(file_path: str, base: str) -> str:
    """Folder = first path component after the base volume (matches runner's derivation)."""
    rel = file_path[len(base.rstrip("/")):].lstrip("/")
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else "root"


def _reingest_folder(folder, files, *, base, index_manager, loader, chunker, downloader) -> int:
    logger.info("=" * 72)
    if not files:
        logger.warning("[Reingest] Folder=%s | no files to ingest", folder)
        return 0

    logger.info("[Reingest] Folder=%s | ingesting %d files (workers=%d, timeout=%ds)",
                folder, len(files), _WORKERS, _FILE_TIMEOUT)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"reingest_{folder}_"))
    folder_chunks = 0
    completed = 0
    try:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futures = {
                pool.submit(
                    _process_and_upsert_file,
                    file_info=f,
                    base_volume_path=base,
                    index_key="supporting",
                    doc_type=DocumentType.SUPPORTING_DOC,
                    doc_source=DocumentSource.SUPPORTING,
                    scope_id="supporting",
                    auditor_folder=folder,
                    loader=loader,
                    chunker=chunker,
                    index_manager=index_manager,
                    downloader=downloader,
                    tmp_dir=tmp_dir,
                ): f["name"]
                for f in files
            }
            for fut in as_completed(futures):
                fname = futures[fut]
                try:
                    folder_chunks += fut.result(timeout=_FILE_TIMEOUT)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Reingest] Worker error | folder=%s | file=%s | error=%s", folder, fname, exc)
                completed += 1
                logger.info(
                    "[Reingest] Folder=%s | %d/%d files | chunks_so_far=%d",
                    folder, completed, len(files), folder_chunks,
                )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    logger.info("[Reingest] Folder=%s | DONE | chunks_ingested=%d", folder, folder_chunks)
    return folder_chunks


def main() -> int:
    settings = get_settings()

    ingest_env = os.environ.get("INGEST_FOLDERS", "").strip()
    folders = (
        [f.strip() for f in ingest_env.split(",") if f.strip()]
        if ingest_env else DEFAULT_INGEST_FOLDERS
    )
    resume_from = os.environ.get("RESUME_FROM", DEFAULT_RESUME_FROM).strip()
    logger.info(
        "[Reingest] Starting | ingest=%s | resume-from(first folder)='%s' | workers=%d | no size-skip",
        folders, resume_from or "(none)", _WORKERS,
    )

    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)

    # The supporting index must exist and be write/query-ready before we upsert.
    index_manager.ensure_index("supporting")

    base = settings.auditors_supporting_docs_volume_path.rstrip("/")
    ocr_provider = _build_ocr_provider(settings)
    loader = DocumentLoader(ocr_provider)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    downloader = DatabricksVolumeDownloader(settings.databricks_host, settings.databricks_token)

    # List the whole supporting-docs volume once, then group files by auditor folder.
    logger.info("[Reingest] Listing supporting-docs volume | path=%s", base)
    all_files = downloader.list_files(base)
    files_by_folder: dict[str, list] = defaultdict(list)
    for f in all_files:
        files_by_folder[_auditor_folder(f["path"], base)].append(f)
    logger.info(
        "[Reingest] Volume file counts by folder: %s",
        {k: len(v) for k, v in sorted(files_by_folder.items())},
    )

    grand_total = 0
    per_folder: dict[str, int] = {}
    for i, folder in enumerate(folders):
        folder_files = sorted(files_by_folder.get(folder, []), key=lambda f: f["name"].lower())

        # Resume applies ONLY to the first folder: start from RESUME_FROM (inclusive).
        if i == 0 and resume_from:
            names = [f["name"] for f in folder_files]
            if resume_from in names:
                start = names.index(resume_from)
                logger.info(
                    "[Reingest] Folder=%s | RESUME from '%s' — skipping %d earlier (already-done) files",
                    folder, resume_from, start,
                )
                folder_files = folder_files[start:]
            else:
                logger.warning(
                    "[Reingest] Folder=%s | RESUME_FROM '%s' not found — ingesting the whole folder",
                    folder, resume_from,
                )

        per_folder[folder] = _reingest_folder(
            folder, folder_files, base=base, index_manager=index_manager,
            loader=loader, chunker=chunker, downloader=downloader,
        )
        grand_total += per_folder[folder]

    index_manager.wait_until_ready("supporting")
    logger.info("=" * 72)
    logger.info("[Reingest] ALL DONE | total_chunks=%d", grand_total)
    for folder, n in per_folder.items():
        logger.info("[Reingest]   %-14s -> %d chunks", folder, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
