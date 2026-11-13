"""Test the robust download + translation on ONE Volume file — e.g. the special-character file that
was 403'ing. Exercises the EXACT pipeline path: SDK-fallback download -> extract -> translate.

Validates two fixes at once:
  1. download_single_file now falls back to the Databricks SDK, which handles names with special /
     fullwidth / circled Unicode ('③', '（ ）', '・') that make the raw Files API return 403.
  2. DocumentLoader translates the extracted text to English (non-Latin auto-detected; French etc.
     need `pip install langdetect`) and saves a <name>__EN.docx to the translated-docs Volume.

Setup:
  pip install langdetect            # so French/Latin-script is detected too (Japanese works without it)
  # .env must have the usual Databricks SP OAuth + Azure OpenAI values the app already uses.
Run:
  python test_download_translate.py
  python test_download_translate.py "/Volumes/nbod_gaagd/cpr_risk_analysis/auditors_supporting_docs/Naeem_6/<file>"
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

try:  # corporate TLS interception
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

# The file that 403'd in the Naeem_6 run (override by passing a path on the command line).
DEFAULT_FILE = (
    "/Volumes/nbod_gaagd/cpr_risk_analysis/auditors_supporting_docs/Naeem_6/"
    "LA_③_業務委託契約書（個人・単回）- Translated.docx"
)


def _h(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def main() -> int:
    from src.config.databricks_auth import bearer_token
    from src.config.settings import get_settings
    from src.ingestion.loader import DocumentLoader
    from src.ingestion.volume_downloader import DatabricksVolumeDownloader
    from src.schemas.state import DocumentType
    from src.services.translation import TranslationService

    volume_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE
    settings = get_settings()
    print(f"host   = {settings.databricks_host}")
    print(f"file   = {volume_path}")

    # ── 1. DOWNLOAD (raw -> SDK fallback) ───────────────────────────────────────────────────────
    _h("1. Download (raw Files API, then SDK fallback for special-character names)")
    downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))
    local = Path(tempfile.gettempdir()) / "dl_test" / Path(volume_path).name
    local.parent.mkdir(parents=True, exist_ok=True)
    ok = downloader.download_single_file(volume_path, local)
    if not ok:
        print("   FAIL — file could not be downloaded by raw OR SDK. If this is 403 for both, it's a "
              "genuine per-file permission/ACL issue (not encoding).")
        return 1
    print(f"   PASS — downloaded {local.stat().st_size:,} bytes -> {local}")

    # ── 2. EXTRACT + TRANSLATE (the full loader path) ───────────────────────────────────────────
    _h("2. Extract + translate to English (same path ingestion uses)")
    try:
        from src.pipeline.runner import _build_ocr_provider

        ocr = _build_ocr_provider(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"   (OCR provider unavailable, continuing without it) | {exc}")
        ocr = None

    translator = TranslationService(settings)
    loader = DocumentLoader(ocr, translator=translator, settings=settings)
    doc = loader.load_file(local, scope_id="supporting", document_type=DocumentType.SUPPORTING_DOC)

    content = doc.content or ""
    translated = bool(doc.metadata.get("translated_to_english"))
    print(f"   extracted/processed {len(content):,} chars")
    print(f"   needs_translation (detector) = {TranslationService.needs_translation(content[:4000])}")
    print(f"   detected language            = {TranslationService.detect_language(content[:4000]) or '(english/undetermined)'}")
    print(f"   translated_to_english flag   = {translated}")
    if translated:
        print(f"   NOTE: a '{Path(volume_path).stem}__EN.docx' was uploaded to the translated-docs Volume.")
    preview = content.strip().replace("\n", " ")[:700]
    print(f"\n   --- CONTENT PREVIEW (first 700 chars; English if translation fired) ---\n   {preview}")

    _h("RESULT")
    print("   Download: PASS")
    print(f"   Translation: {'PASS (translated to English)' if translated else 'NOT TRIGGERED (already English, or langdetect missing for a Latin-script language)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
