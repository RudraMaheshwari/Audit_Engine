"""Resume / incremental supporting-docs ingestion for ONE auditor.

Lists the auditor's supporting-docs volume folder, finds which file names are ALREADY in the
supporting index (by `document_name`), and ingests ONLY the missing ones — so a run that stopped
part-way (e.g. token expiry) can be completed without re-embedding everything.

Uses the per-auditor folder (<auditors_supporting_docs_volume_path>/<auditor>), never the parent, so
it can't cross-contaminate. Download uses the hardened path (raw → token refresh → SDK fallback) and
non-English docs are translated, exactly like a normal run.

Translation-reuse catch: a prior run may have TRANSLATED a file (saved <stem>__EN.<ext> to the
translated-docs volume) but then FAILED to push it to the index. So for each missing file this script
first looks for that already-translated copy — if present it ingests the copy DIRECTLY (no
re-translation), keeping the ORIGINAL file name as document_name; only when no usable copy exists does
it translate from the source and ingest.

Usage:
    python scripts/ingest_missing_supporting_docs.py --auditor Naeem_6
    python scripts/ingest_missing_supporting_docs.py --auditor Naeem_6 --dry-run      # list only
    python scripts/ingest_missing_supporting_docs.py --auditor Naeem_6 --workers 4
"""

import argparse
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
from src.schemas.state import DocumentType
from src.services.translation import TranslationService
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

# Reuse the exact pipeline helpers (download → extract → translate → chunk → upsert).
from src.pipeline.runner import _build_ocr_provider, _process_and_upsert_file
# Translated copies are named <stem>__EN.<ext>, where the ext follows the source family (xlsx/csv/docx).
from src.pipeline.translated_docs import _renderer_for

logger = get_logger(__name__)

_SCAN_LIMIT = 10000   # max chunks pulled to enumerate already-ingested document_names


def _already_ingested_names(index_manager: VectorIndexManager, index_key: str, auditor: str) -> set[str]:
    """Distinct document_name values already present in the index for this auditor_folder."""
    try:
        chunks = index_manager.similarity_search(
            index_key=index_key, query="document", scope_id=None,
            top_k=_SCAN_LIMIT, filters={"auditor_folder": auditor},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Resume] Could not scan index (treating as empty) | %s", exc)
        return set()
    if len(chunks) >= _SCAN_LIMIT:
        logger.warning("[Resume] Index scan hit the %d-chunk cap — the 'already ingested' set may be "
                       "incomplete. A few files could be re-ingested (harmless).", _SCAN_LIMIT)
    return {(c.document_name or "").strip() for c in chunks if c.document_name}


def _list_translated_copies(downloader, settings, auditor: str) -> dict:
    """Map translated filename ("<stem>__EN.<ext>") -> its volume file_info, for this auditor's
    translated-docs folder. Empty when the folder isn't configured / doesn't exist / is empty."""
    base = (getattr(settings, "translated_docs_volume_path", "") or "").rstrip("/")
    if not base:
        return {}
    folder = f"{base}/{auditor}"
    try:
        files = downloader.list_files(folder)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Resume] Could not list translated-docs folder %s | %s", folder, exc)
        return {}
    return {f.get("name", "").strip(): f for f in (files or []) if f.get("name")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest only the missing supporting docs for an auditor")
    parser.add_argument("--auditor", required=True, help="auditor folder id (e.g. Naeem_6)")
    parser.add_argument("--workers", type=int, default=4, help="concurrent file workers (default 4)")
    parser.add_argument("--dry-run", action="store_true", help="only list what's missing, don't ingest")
    args = parser.parse_args()

    settings = get_settings()
    # Run AS this auditor so derived paths (translated-docs folder, etc.) use it — not the .env default.
    settings.auditor_id = args.auditor
    logger.info("[Resume] auditor=%s | translation_engine=%s",
                args.auditor, getattr(settings, "translation_engine", "openai"))
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)
    index_manager.ensure_index("supporting")

    # Per-auditor folder (never the parent) — matches how a correct run derives it.
    base = (settings.auditors_supporting_docs_volume_path or "").rstrip("/")
    folder = f"{base}/{args.auditor}"
    downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))

    all_files = downloader.list_files(folder)
    if not all_files:
        logger.warning("[Resume] No files found in %s — nothing to do.", folder)
        return
    ingested = _already_ingested_names(index_manager, "supporting", args.auditor)
    missing = [f for f in all_files if (f.get("name", "").strip() not in ingested)]

    # Previously-translated copies for this auditor (from a run that translated but failed to push).
    translated_present = _list_translated_copies(downloader, settings, args.auditor)

    def _translated_match(name: str):
        """The translated copy's file_info for an original file name, or None."""
        ext, _ = _renderer_for(name)
        return translated_present.get(f"{Path(name).stem}__EN{ext}")

    logger.info("[Resume] folder=%s | total=%d | already_ingested=%d | missing=%d | translated_copies_available=%d",
                folder, len(all_files), len(ingested), len(missing), len(translated_present))
    for f in missing:
        nm = f.get("name", "")
        route = "TRANSLATED-COPY" if _translated_match(nm.strip()) else "needs-translation"
        logger.info("[Resume]   MISSING -> %s  [%s]", nm, route)

    if args.dry_run:
        logger.info("[Resume] --dry-run: no ingestion performed.")
        return
    if not missing:
        logger.info("[Resume] Nothing missing — index is complete for %s.", args.auditor)
        return

    ocr = _build_ocr_provider(settings)
    loader = DocumentLoader(ocr, translator=TranslationService(settings), settings=settings)
    # Second loader with NO translator — ingests an already-translated __EN copy as-is (no re-translate).
    loader_plain = DocumentLoader(ocr, translator=None, settings=settings)
    chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    tmp_dir = Path(tempfile.mkdtemp(prefix="resume_supporting_"))
    failures: list[str] = []
    reused = 0
    total_chunks = 0
    done = 0

    def _ingest_translated_copy(original_name: str, tinfo: dict) -> int:
        """Ingest an existing __EN translated copy directly (no re-translation). Keeps the ORIGINAL
        file name as document_name so the index matches a normal run. Returns chunks (0 = unusable)."""
        local = tmp_dir / f"EN__{Path(tinfo['name']).name}"
        if not downloader.download_single_file(tinfo["path"], local):
            logger.warning("[Resume] Translated copy download failed | %s", tinfo.get("path"))
            return 0
        try:
            doc = loader_plain.load_file(local, scope_id="supporting",
                                         document_type=DocumentType.SUPPORTING_DOC)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Resume] Translated copy extract failed | %s | %s", tinfo.get("name"), exc)
            return 0
        finally:
            try:
                local.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        doc.file_name = original_name   # chunk.document_name derives from this -> keep the original name
        chunks = chunker.chunk(doc, DocumentSource.SUPPORTING)
        if not chunks:
            return 0
        chunks = [c.model_copy(update={"auditor_folder": args.auditor}) for c in chunks]
        index_manager.upsert_chunks("supporting", chunks)
        logger.info("[Resume] ✔ Ingested from TRANSLATED copy | file=%s (via %s) | chunks=%d",
                    original_name, tinfo.get("name"), len(chunks))
        return len(chunks)

    def _ingest_one(f: dict) -> tuple[int, bool]:
        """Ingest one missing file; reuse its translated copy when available. (chunks, used_copy)."""
        name = f.get("name", "").strip()
        tinfo = _translated_match(name)
        if tinfo is not None:
            n = _ingest_translated_copy(name, tinfo)
            if n > 0:
                return n, True
            logger.info("[Resume] Translated copy unusable for %s — re-translating from source", name)
        # No usable copy — translate from the source exactly like a normal run.
        n = _process_and_upsert_file(
            file_info=f, base_volume_path=folder, index_key="supporting",
            doc_type=DocumentType.SUPPORTING_DOC, doc_source=DocumentSource.SUPPORTING,
            scope_id="supporting", auditor_folder=args.auditor, loader=loader, chunker=chunker,
            index_manager=index_manager, downloader=downloader, tmp_dir=tmp_dir, failures=failures,
        )
        return n, False

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {pool.submit(_ingest_one, f): f.get("name") for f in missing}
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    n, used_copy = fut.result(timeout=600)
                    total_chunks += n
                    if used_copy:
                        reused += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Resume] Worker error | file=%s | %s", name, exc)
                done += 1
                logger.info("[Resume] Progress | %d/%d files | chunks_so_far=%d", done, len(missing), total_chunks)
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    index_manager.wait_until_ready("supporting")
    if failures:
        logger.error("[Resume] %d file(s) could NOT be downloaded and were NOT ingested | %s",
                     len(failures), failures)
    retranslated = len(missing) - len(failures) - reused
    logger.info("[Resume] DONE | ingested=%d | reused_translated=%d | re-translated=%d | "
                "download_failed=%d | chunks_added=%d",
                len(missing) - len(failures), reused, retranslated, len(failures), total_chunks)


if __name__ == "__main__":
    main()
