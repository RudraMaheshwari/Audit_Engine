"""Re-translate ONE supporting-doc file and save the translation in the SOURCE format.

Use case: a spreadsheet (.xlsx) was translated earlier but the English copy was saved as a .docx, so a
reviewer can't open it as a sheet. This script re-does the translation for that single file and writes
the English copy back in the matching format (xlsx -> .xlsx, csv -> .csv, else .docx), under:
    <translated_docs_volume_path>/<auditor>/<original_stem>__EN.<ext>

It does NOT touch any vector index — it only produces the reviewable translated file.

The source file is read from the auditor's supporting-docs Databricks UC Volume (over the Files API) —
nothing is read from local disk. The folder is resolved exactly like the pipeline does, or you can point
at an exact volume path with --folder.

Usage:
    python scripts/retranslate_one_file.py --auditor Naeem_7 \
        --file "71 Alliance Management_2026_Sell out and inventory data.xlsx"

    # point at an exact Databricks volume folder (if the file isn't under the derived path):
    python scripts/retranslate_one_file.py --auditor Naeem_7 --file "71 Alliance ..." \
        --folder /Volumes/nbod_gaagd/cpr_risk_analysis/auditors_supporting_docs/Naeem_7

    # also delete the stale, wrongly-saved <stem>__EN.docx after writing the .xlsx:
    python scripts/retranslate_one_file.py --auditor Naeem_7 --file "71 Alliance ..." --remove-docx
"""

import argparse
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.databricks_auth import bearer_token
from src.config.settings import get_settings
from src.ingestion.extractors import ExtractorFactory
from src.ingestion.volume_downloader import DatabricksVolumeDownloader
from src.pipeline.runner import _build_ocr_provider
from src.pipeline.translated_docs import save_translated_doc
from src.services.translation import TranslationService
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _find_file(files: list[dict], query: str) -> dict | None:
    """Match the requested file by name: exact (case-insensitive) first, then substring either way."""
    q = query.strip().lower()
    for f in files:
        if (f.get("name", "") or "").strip().lower() == q:
            return f
    matches = [f for f in files
               if q in (f.get("name", "") or "").lower() or (f.get("name", "") or "").lower() in q]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning("[Retranslate] Multiple candidates matched — pass the exact name:")
        for f in matches:
            logger.warning("[Retranslate]   candidate -> %s", f.get("name"))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-translate one supporting-doc file, saved in source format")
    parser.add_argument("--auditor", default="Naeem_7", help="auditor folder id (default Naeem_7)")
    parser.add_argument("--file", required=True, help="file name (or unique substring) to re-translate")
    parser.add_argument("--folder", default="",
                        help="explicit Databricks volume folder to search (e.g. "
                             "/Volumes/.../auditors_supporting_docs/Naeem_7). Overrides the derived path.")
    parser.add_argument("--remove-docx", action="store_true",
                        help="delete the stale <stem>__EN.docx in the translated-docs volume after writing")
    args = parser.parse_args()

    settings = get_settings()
    settings.auditor_id = args.auditor   # so derived paths + the translated copy use this auditor

    # Databricks volume folder to search: the explicit --folder if given, else the SAME resolution the
    # pipeline uses (honours a per-run supporting_docs_path, else <base volume>/<auditor_id>).
    # This is a Databricks UC Volume path — the file is fetched over the Files API, never read locally.
    folder = (args.folder.strip() or settings.resolved_supporting_docs_folder).rstrip("/")
    downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))

    logger.info("[Retranslate] Searching Databricks volume folder (recursive): %s", folder)
    files = downloader.list_files(folder)
    if not files:
        logger.error("[Retranslate] No files found under Databricks folder %s | check the path / --folder", folder)
        return
    logger.info("[Retranslate] Found %d file(s) under the Databricks folder", len(files))

    target = _find_file(files, args.file)
    if not target:
        logger.error("[Retranslate] File not found (or ambiguous) in %s | query=%r", folder, args.file)
        return
    file_name = target["name"]
    logger.info("[Retranslate] Target file -> %s", file_name)

    tmp_dir = Path(tempfile.mkdtemp(prefix="retranslate_"))
    local_path = tmp_dir / file_name
    try:
        if not downloader.download_single_file(target["path"], local_path):
            logger.error("[Retranslate] Download failed | file=%s", file_name)
            return

        # Extract text (Excel extractor flattens cells to ' | ' rows), then translate to English.
        ocr = _build_ocr_provider(settings)
        extractor = ExtractorFactory(ocr).get(local_path)
        doc = extractor.extract(local_path, "supporting")
        logger.info("[Retranslate] Extracted | file=%s | chars=%d", file_name, len(doc.content or ""))

        translator = TranslationService(settings)
        if not translator.enabled:
            logger.error("[Retranslate] Translation is disabled (TRANSLATE_TO_ENGLISH) — nothing to do.")
            return
        if not translator.needs_translation(doc.content or ""):
            logger.warning("[Retranslate] File looks already-English — saving the extracted text as-is.")
            english = doc.content or ""
        else:
            english = translator.translate_text(doc.content or "", label=file_name)

        dest = save_translated_doc(settings, file_name, english, auditor_id=args.auditor)
        if dest:
            logger.info("[Retranslate] ✔ Saved translated copy -> %s", dest)
        else:
            logger.error("[Retranslate] Save failed — see warnings above.")
            return

        if args.remove_docx:
            stem = Path(file_name).stem
            tdbase = (getattr(settings, "translated_docs_volume_path", "") or "").rstrip("/")
            stale = f"{tdbase}/{args.auditor}/{stem}__EN.docx"
            try:
                from databricks.sdk import WorkspaceClient

                from src.config.databricks_auth import build_config

                WorkspaceClient(config=build_config(settings)).files.delete(stale)
                logger.info("[Retranslate] Removed stale docx -> %s", stale)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Retranslate] Could not remove stale docx (%s) | %s", stale, exc)
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
