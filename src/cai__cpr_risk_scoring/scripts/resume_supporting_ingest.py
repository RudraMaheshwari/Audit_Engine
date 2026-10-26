"""
Resume supporting-docs ingestion from a specific folder (and optionally file).

Usage:
    python scripts/resume_supporting_ingest.py --start-folder Sam_1
    python scripts/resume_supporting_ingest.py --start-folder Sam_1 --start-file "Tab 4 item 49.xlsx"

All files in folders alphabetically before --start-folder are skipped.
Within --start-folder, all files alphabetically before --start-file are skipped.
Every file that can be extracted is inserted — no chunk limit.
"""

import argparse
import shutil
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.ingestion.chunker import Chunker
from src.ingestion.extractors.ocr.azure_document_intelligence_provider import AzureDocumentIntelligenceOCRProvider
from src.ingestion.extractors.ocr.tesseract_provider import TesseractOCRProvider
from src.ingestion.loader import DocumentLoader
from src.ingestion.volume_downloader import DatabricksVolumeDownloader
from src.models.schemas import DocumentSource
from src.schemas.state import DocumentType
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger(__name__)

_INGEST_FILE_WORKERS = 4


def _build_ocr_provider(settings):
    if settings.azure_document_intelligence_endpoint and settings.azure_document_intelligence_key:
        logger.info("[Resume] OCR provider: Azure Document Intelligence")
        return AzureDocumentIntelligenceOCRProvider(
            endpoint=settings.azure_document_intelligence_endpoint,
            api_key=settings.azure_document_intelligence_key,
        )
    logger.warning("[Resume] Azure DI credentials not set — falling back to Tesseract OCR")
    return TesseractOCRProvider(lang="eng+jpn", tesseract_cmd=settings.tesseract_cmd)


def _auditor_folder(file_path: str, base: str) -> str:
    rel = file_path[len(base):].lstrip("/")
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else "root"


def _process_file(
    *,
    file_info: dict,
    base_volume_path: str,
    auditor_folder: str,
    loader,
    chunker,
    index_manager,
    downloader,
    tmp_dir: Path,
) -> int:
    """Download → extract → chunk → upsert. Returns chunk count (0 on any failure)."""
    import os
    file_path = file_info["path"]
    file_name = file_info["name"]

    rel = file_path[len(base_volume_path.rstrip("/")):].lstrip("/")
    local_path = tmp_dir / rel

    logger.info("[Resume] ▶ Downloading | folder=%s | file=%s", auditor_folder, file_name)
    ok = downloader.download_single_file(file_path, local_path)
    if not ok:
        logger.warning("[Resume] Download failed — skipping | file=%s", file_name)
        return 0

    logger.info("[Resume] ⚙ Extracting  | folder=%s | file=%s", auditor_folder, file_name)
    try:
        doc = loader.load_file(local_path, scope_id="supporting", document_type=DocumentType.SUPPORTING_DOC)
    except Exception as exc:
        logger.warning("[Resume] Extraction failed | file=%s | error=%s", file_name, exc)
        return 0
    finally:
        try:
            local_path.unlink(missing_ok=True)
        except Exception:
            pass

    chunks = chunker.chunk(doc, DocumentSource.SUPPORTING)
    if not chunks:
        logger.warning("[Resume] No chunks produced | file=%s", file_name)
        return 0

    chunks = [c.model_copy(update={"auditor_folder": auditor_folder}) for c in chunks]

    logger.info("[Resume] ↑ Upserting   | folder=%s | file=%s | chunks=%d", auditor_folder, file_name, len(chunks))
    try:
        index_manager.upsert_chunks("supporting", chunks)
    except Exception as exc:
        logger.warning("[Resume] Upsert failed | file=%s | error=%s", file_name, exc)
        return 0

    logger.info("[Resume] ✔ Done        | folder=%s | file=%s | chunks=%d", auditor_folder, file_name, len(chunks))
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume supporting-docs ingestion from a checkpoint.")
    parser.add_argument("--start-folder", required=True,
                        help="Auditor folder name to resume from (e.g. Sam_1)")
    parser.add_argument("--start-file",   default=None,
                        help="File name within start-folder to resume from (optional)")
    args = parser.parse_args()

    start_folder = args.start_folder.strip()
    start_file   = args.start_file.strip() if args.start_file else None

    logger.info("[Resume] Config | start_folder=%s | start_file=%s",
                start_folder, start_file or "(first)")

    settings = get_settings()
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)
    index_manager.ensure_index("supporting")

    ocr_provider = _build_ocr_provider(settings)
    loader     = DocumentLoader(ocr_provider)
    chunker    = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    downloader = DatabricksVolumeDownloader(settings.databricks_host, settings.databricks_token)

    volume_path = settings.auditors_supporting_docs_volume_path
    base = volume_path.rstrip("/")

    logger.info("[Resume] Listing all supporting docs | volume=%s", volume_path)
    all_files = downloader.list_files(volume_path)
    if not all_files:
        logger.warning("[Resume] No files found — exiting")
        return

    folder_counts: dict[str, int] = defaultdict(int)
    for f in all_files:
        folder_counts[_auditor_folder(f["path"], base)] += 1
    for folder, count in sorted(folder_counts.items()):
        logger.info("[Resume] Folder | %s | files=%d", folder, count)

    # Filter to checkpoint
    resume_files = []
    for f in all_files:
        folder = _auditor_folder(f["path"], base)
        if folder < start_folder:
            continue
        if folder == start_folder and start_file and f["name"] < start_file:
            continue
        resume_files.append(f)

    logger.info("[Resume] Files to process: %d", len(resume_files))

    tmp_dir = Path(tempfile.mkdtemp(prefix="audit_resume_supporting_"))
    total_chunks = 0
    completed    = 0

    try:
        with ThreadPoolExecutor(max_workers=_INGEST_FILE_WORKERS) as pool:
            futures = {
                pool.submit(
                    _process_file,
                    file_info=f,
                    base_volume_path=volume_path,
                    auditor_folder=_auditor_folder(f["path"], base),
                    loader=loader,
                    chunker=chunker,
                    index_manager=index_manager,
                    downloader=downloader,
                    tmp_dir=tmp_dir,
                ): f["name"]
                for f in resume_files
            }
            for fut in as_completed(futures):
                fname = futures[fut]
                try:
                    n = fut.result()
                    total_chunks += n
                except Exception as exc:
                    logger.warning("[Resume] Worker error | file=%s | error=%s", fname, exc)
                completed += 1
                logger.info("[Resume] Progress | %d/%d files | chunks_so_far=%d",
                            completed, len(resume_files), total_chunks)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    logger.info(
        "[Resume] Done | files_processed=%d | chunks_added=%d",
        completed, total_chunks,
    )
    index_manager.wait_until_ready("supporting")


if __name__ == "__main__":
    main()
