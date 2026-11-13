"""Supporting-docs VOLUME statistics across ALL auditors (read-only, from the Databricks Volume folder).

Lists the per-auditor supporting-docs folders under the base volume and reports:
  - number of files (docs) per auditor + the AVERAGE docs/auditor
  - file SIZE in bytes (max + average)
  - the LARGEST file PER AUDITOR and its extracted CHARACTER COUNT
      * plain-text files (.txt/.csv/.tsv/.json/.md/.log) are counted directly (cheap, streamed)
      * office/PDF files are run through the ingestion DocumentLoader (OCR) — skipped above a size cap
        so a huge PPTX/PDF doesn't trigger a massive OCR run unless you force it

Reads the volume directly (NOT the vector index). Read-only.

    python scripts/supporting_docs_stats.py
    python scripts/supporting_docs_stats.py --auditor Naeem_7
    python scripts/supporting_docs_stats.py --no-chars                # skip all char counting
    python scripts/supporting_docs_stats.py --extract-max-mib 0       # force OCR even on huge files
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))


def _fix_tls() -> None:
    """Trust the OS/corporate root CA (SSL fix) before any HTTPS client — best-effort."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass


_fix_tls()

from src.config.databricks_auth import bearer_token  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.ingestion.volume_downloader import DatabricksVolumeDownloader  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("supporting_docs_stats")

_MIB = 1_048_576
_TEXT_EXTS = {".txt", ".csv", ".tsv", ".json", ".md", ".log"}

# Lazily-built DocumentLoader (office/PDF extraction) — created once, only if needed.
_loader_cache: dict = {}


def _auditor_of(path: str, base: str) -> str:
    rest = path[len(base):].lstrip("/") if path.startswith(base) else path.lstrip("/")
    parts = rest.split("/")
    return parts[0] if parts and parts[0] else "(root)"


def _fmt(n) -> str:
    return f"{n:,.1f}" if isinstance(n, float) else f"{n:,}"


def _stream_text_chars(local_path: Path) -> int:
    """Character count of a text file, read in 1 MiB chunks so a huge file doesn't blow up memory."""
    total = 0
    with open(local_path, "rb") as fh:
        reader = io.TextIOWrapper(fh, encoding="utf-8", errors="ignore")
        while True:
            buf = reader.read(_MIB)
            if not buf:
                break
            total += len(buf)
    return total


def _loader(settings):
    if "loader" not in _loader_cache:
        from src.ingestion.loader import DocumentLoader
        from src.services.translation import TranslationService
        from src.pipeline.runner import _build_ocr_provider
        _loader_cache["loader"] = DocumentLoader(
            _build_ocr_provider(settings), translator=TranslationService(settings), settings=settings
        )
    return _loader_cache["loader"]


def _char_count(settings, downloader, file_path: str, size: int, extract_max_mib: float) -> tuple:
    """Return (char_count|None, note). Text files are streamed; office/PDF use the loader (size-capped)."""
    ext = Path(file_path).suffix.lower()
    is_text = ext in _TEXT_EXTS
    if not is_text and extract_max_mib > 0 and size > extract_max_mib * _MIB:
        return None, f"skipped (>{extract_max_mib:g} MiB office/PDF — use --extract-max-mib 0 to force)"

    tmp_dir = Path(tempfile.mkdtemp(prefix="supp_stats_"))
    tmp = tmp_dir / Path(file_path).name
    try:
        if not downloader.download_single_file(file_path, tmp):
            return None, "download failed"
        if is_text:
            return _stream_text_chars(tmp), "text"
        from src.schemas.state import DocumentType
        doc = _loader(settings).load_file(tmp, scope_id="supporting",
                                          document_type=DocumentType.SUPPORTING_DOC)
        return len(doc.content or ""), "extracted"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SuppStats] char-count failed | file=%s | %s", file_path, exc)
        return None, f"error: {str(exc)[:60]}"
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    p = argparse.ArgumentParser(description="Supporting-docs VOLUME stats across auditors (read-only).")
    p.add_argument("--auditor", default=None, help="limit to one auditor folder (default: all)")
    p.add_argument("--top", type=int, default=10, help="how many of the largest files to list")
    p.add_argument("--no-chars", action="store_true", help="skip all char counting")
    p.add_argument("--extract-max-mib", type=float, default=50.0,
                   help="skip OCR char-count for office/PDF files larger than this (0 = no cap). "
                        "Text files are always counted.")
    args = p.parse_args()
    only = (args.auditor or "").strip() or None

    settings = get_settings()
    base = settings.auditors_supporting_docs_volume_path.rstrip("/")
    downloader = DatabricksVolumeDownloader(settings.databricks_host, bearer_token(settings))

    scan_root = f"{base}/{only}" if only else base
    logger.info("[SuppStats] Listing volume | path=%s", scan_root)
    files = downloader.list_files(scan_root)
    if not files:
        print(f"\nNo files found under {scan_root}\n")
        return 0

    per_auditor_files: dict[str, int] = defaultdict(int)
    per_auditor_bytes: dict[str, int] = defaultdict(int)
    biggest_per_auditor: dict[str, dict] = {}
    sizes: list[int] = []

    for f in files:
        path = f.get("path") or ""
        size = int(f.get("size") or 0)
        aud = only or _auditor_of(path, base)
        per_auditor_files[aud] += 1
        per_auditor_bytes[aud] += size
        sizes.append(size)
        cur = biggest_per_auditor.get(aud)
        if cur is None or size > cur["size"]:
            biggest_per_auditor[aud] = {"path": path, "size": size}

    n_auditors = len(per_auditor_files)
    n_files = len(sizes)
    total_bytes = sum(sizes)

    print(f"\n{'='*66}\nSUPPORTING-DOCS VOLUME STATS\n{base}\n{'='*66}")
    print(f"auditors              : {n_auditors}")
    print(f"total files (docs)    : {n_files}")
    print(f"total size            : {_fmt(total_bytes)} bytes ({total_bytes/_MIB:,.1f} MiB)")
    print(f"AVG docs / auditor    : {_fmt(n_files / n_auditors)}")
    print(f"max file size         : {_fmt(max(sizes))} bytes ({max(sizes)/_MIB:,.2f} MiB)")
    print(f"avg file size         : {_fmt(total_bytes / n_files)} bytes ({(total_bytes/n_files)/1024:,.1f} KiB)")

    # Largest file PER AUDITOR + its character count.
    print(f"\n--- LARGEST file per auditor  (chars = extracted text length) ---")
    header = f"{'auditor':20s} {'size (MiB)':>11} {'chars':>14}  file"
    print(header)
    print("-" * len(header))
    for aud in sorted(biggest_per_auditor, key=lambda a: -biggest_per_auditor[a]["size"]):
        big = biggest_per_auditor[aud]
        if args.no_chars:
            chars_str = "(skipped)"
        else:
            logger.info("[SuppStats] char count | auditor=%s | file=%s", aud, Path(big["path"]).name)
            count, note = _char_count(settings, downloader, big["path"], big["size"], args.extract_max_mib)
            chars_str = _fmt(count) if count is not None else note
        print(f"{aud:20s} {big['size']/_MIB:>11,.2f} {chars_str:>14}  {Path(big['path']).name}")

    print(f"\n--- TOP {args.top} files by size (all auditors) ---")
    print(f"{'bytes':>14}  auditor / file")
    print("-" * 70)
    for f in sorted(files, key=lambda x: int(x.get('size') or 0), reverse=True)[:args.top]:
        path = f.get("path") or ""
        print(f"{int(f.get('size') or 0):>14,}  {only or _auditor_of(path, base)} / {Path(path).name}")

    print(f"\n--- documents per auditor ---")
    for aud, n in sorted(per_auditor_files.items(), key=lambda kv: -kv[1]):
        print(f"  {aud:35s} docs={n:>5}  size={per_auditor_bytes[aud]/_MIB:>8,.1f} MiB")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
