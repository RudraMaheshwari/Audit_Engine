"""Ingest every document in a Databricks Volume path into the `supporting` index under ONE auditor
folder.

Default: ingest all files under /Volumes/nbod_gaagd/cpr_risk_analysis/additional_docs and tag them as
auditor_folder=Naeem_1, so they are retrieved alongside Naeem_1's other supporting docs (same index,
same schema, same chunking/OCR as the normal pipeline). Upserts are idempotent by chunk_id, so
re-running is safe.

Run (from the project root, with the venv active and .env configured):
    python scripts/ingest_additional_docs.py

Env overrides:
    set SOURCE_PATH=/Volumes/nbod_gaagd/cpr_risk_analysis/additional_docs
    set AUDITOR_FOLDER=Naeem_1
    set WORKERS=4          (parallel files; default matches the main app — raise to 6-8 for small files)
    set FILE_TIMEOUT=1800  (seconds per file; big spreadsheets need time to embed + upsert)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Make `src` importable when run directly as `python scripts/ingest_additional_docs.py`
# (adds the project root — the parent of this scripts/ folder — to sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.ingestion.chunker import Chunker
from src.ingestion.loader import DocumentLoader
from src.ingestion.volume_downloader import DatabricksVolumeDownloader
from src.models.schemas import DocumentSource
from src.pipeline.runner import _build_ocr_provider, _process_and_upsert_file
from src.schemas.state import DocumentType
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger("ingest_additional_docs")

_DEFAULT_SOURCE = "/Volumes/nbod_gaagd/cpr_risk_analysis/additional_docs"
_DEFAULT_AUDITOR_FOLDER = "Naeem_1"

_WORKERS = int(os.environ.get("WORKERS", "4"))  # matches the main app's _INGEST_FILE_WORKERS (fast)
_FILE_TIMEOUT = int(os.environ.get("FILE_TIMEOUT", "1800"))


def main() -> int:
    settings = get_settings()
    source_path = os.environ.get("SOURCE_PATH", _DEFAULT_SOURCE).strip().rstrip("/")
    auditor_folder = os.environ.get("AUDITOR_FOLDER", _DEFAULT_AUDITOR_FOLDER).strip()

    logger.info(
        "[AdditionalDocs] Start | source=%s | auditor_folder=%s | workers=%d | timeout=%ds",
        source_path, auditor_folder, _WORKERS, _FILE_TIMEOUT,
    )

    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)
    # The supporting index must exist and be write/query-ready before we upsert.
    index_manager.ensure_index("supporting")

    ocr_provider = _build_ocr_provider(settings)
    loader = DocumentLoader(ocr_provider)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    downloader = DatabricksVolumeDownloader(settings.databricks_host, settings.databricks_token)

    logger.info("[AdditionalDocs] Listing volume | path=%s", source_path)
    files = sorted(downloader.list_files(source_path), key=lambda f: f["name"].lower())
    if not files:
        logger.warning("[AdditionalDocs] No files found under %s — nothing to ingest", source_path)
        return 0
    logger.info("[AdditionalDocs] Found %d file(s) to ingest", len(files))

    tmp_dir = Path(tempfile.mkdtemp(prefix="additional_docs_"))
    total_chunks = 0
    completed = 0
    try:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futures = {
                pool.submit(
                    _process_and_upsert_file,
                    file_info=f,
                    base_volume_path=source_path,
                    index_key="supporting",
                    doc_type=DocumentType.SUPPORTING_DOC,
                    doc_source=DocumentSource.SUPPORTING,
                    scope_id="supporting",
                    auditor_folder=auditor_folder,
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
                    total_chunks += fut.result(timeout=_FILE_TIMEOUT)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[AdditionalDocs] Worker error | file=%s | error=%s", fname, exc)
                completed += 1
                logger.info(
                    "[AdditionalDocs] %d/%d files | chunks_so_far=%d", completed, len(files), total_chunks,
                )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    index_manager.wait_until_ready("supporting")
    logger.info(
        "[AdditionalDocs] DONE | files=%d | total_chunks=%d | auditor_folder=%s | index=supporting",
        len(files), total_chunks, auditor_folder,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
