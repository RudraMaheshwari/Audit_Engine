"""Ingest an auditor's supporting docs from a Volume folder into the supporting index.

For a NEW auditor this ingests the whole folder; for an existing one it skips files already present
(by document_name) and ingests the rest — so it's safe to re-run and doubles as a resume. Download is
hardened (raw -> token refresh -> SDK fallback for any Unicode name) and non-English docs are
translated, exactly like a normal run.

Usage:
    python scripts/ingest_auditor_supporting_docs.py --auditor Naeem_7
    python scripts/ingest_auditor_supporting_docs.py --auditor Naeem_7 \
        --path /Volumes/nbod_gaagd/cpr_risk_analysis/auditors_supporting_docs/Naeem_7/
    python scripts/ingest_auditor_supporting_docs.py --auditor Naeem_7 --all        # re-ingest everything
    python scripts/ingest_auditor_supporting_docs.py --auditor Naeem_7 --dry-run     # list only
"""

import argparse
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.databricks_auth import bearer_token
from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.ingestion.chunker import Chunker
from src.ingestion.loader import DocumentLoader
from src.ingestion.volume_downloader import DatabricksVolumeDownloader
from src.models.schemas import DocumentSource
from src.pipeline.runner import _build_ocr_provider, _process_and_upsert_file
from src.schemas.state import DocumentType
from src.services.translation import TranslationService
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger(__name__)

_SCAN_LIMIT = 10000   # max chunks pulled to enumerate already-ingested document_names


def _already_ingested_names(index_manager: VectorIndexManager, auditor: str) -> set[str]:
    try:
        chunks = index_manager.similarity_search(
            index_key="supporting", query="document", scope_id=None,
            top_k=_SCAN_LIMIT, filters={"auditor_folder": auditor},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Ingest] Could not scan index (treating as empty) | %s", exc)
        return set()
    if len(chunks) >= _SCAN_LIMIT:
        logger.warning("[Ingest] Index scan hit the %d-chunk cap — 'already ingested' may be incomplete.",
                       _SCAN_LIMIT)
    return {(c.document_name or "").strip() for c in chunks if c.document_name}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest an auditor's supporting docs from a Volume folder")
    parser.add_argument("--auditor", required=True, help="auditor folder id / label (e.g. Naeem_7)")
    parser.add_argument("--path", default="", help="Volume folder to ingest (default: <base>/<auditor>)")
    parser.add_argument("--workers", type=int, default=4, help="concurrent file workers (default 4)")
    parser.add_argument("--all", action="store_true", help="ingest ALL files (don't skip already-indexed)")
    parser.add_argument("--dry-run", action="store_true", help="list what would be ingested, don't ingest")
    args = parser.parse_args()

    settings = get_settings()
    # Run AS this auditor so derived paths (translated-docs folder, etc.) use it — not the .env default.
    settings.auditor_id = args.auditor
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)
    index_manager.ensure_index("supporting")

    base = (settings.auditors_supporting_docs_volume_path or "").rstrip("/")
    folder = (args.path or f"{base}/{args.auditor}").rstrip("/")
    downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))

    all_files = downloader.list_files(folder)
    if not all_files:
        logger.warning("[Ingest] No files found in %s — nothing to do.", folder)
        return

    ingested = set() if args.all else _already_ingested_names(index_manager, args.auditor)
    todo = [f for f in all_files if args.all or (f.get("name", "").strip() not in ingested)]

    logger.info("[Ingest] folder=%s | auditor=%s | total=%d | already_ingested=%d | to_ingest=%d",
                folder, args.auditor, len(all_files), len(ingested), len(todo))
    for f in todo:
        logger.info("[Ingest]   -> %s", f.get("name"))

    if args.dry_run:
        logger.info("[Ingest] --dry-run: no ingestion performed.")
        return
    if not todo:
        logger.info("[Ingest] Nothing to ingest — index already complete for %s.", args.auditor)
        return

    ocr = _build_ocr_provider(settings)
    loader = DocumentLoader(ocr, translator=TranslationService(settings), settings=settings)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    tmp_dir = Path(tempfile.mkdtemp(prefix="ingest_auditor_"))
    failures: list[str] = []
    total_chunks = 0
    done = 0

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {
                pool.submit(
                    _process_and_upsert_file,
                    file_info=f, base_volume_path=folder, index_key="supporting",
                    doc_type=DocumentType.SUPPORTING_DOC, doc_source=DocumentSource.SUPPORTING,
                    scope_id="supporting", auditor_folder=args.auditor, loader=loader, chunker=chunker,
                    index_manager=index_manager, downloader=downloader, tmp_dir=tmp_dir, failures=failures,
                ): f["name"]
                for f in todo
            }
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    total_chunks += fut.result(timeout=600)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Ingest] Worker error | file=%s | %s", name, exc)
                done += 1
                logger.info("[Ingest] Progress | %d/%d files | chunks_so_far=%d", done, len(todo), total_chunks)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    index_manager.wait_until_ready("supporting")
    if failures:
        logger.error("[Ingest] %d file(s) could NOT be downloaded and were NOT ingested | %s",
                     len(failures), failures)
    logger.info("[Ingest] DONE | auditor=%s | ingested=%d | download_failed=%d | chunks_added=%d",
                args.auditor, len(todo) - len(failures), len(failures), total_chunks)


if __name__ == "__main__":
    main()
