"""One-off utility — delete an auditor's chunks from the SUPPORTING-DOCS vector index.

Removes ALL chunks whose `auditor_folder` matches each given name from the `supporting` index
(the index is shared across auditors, partitioned by auditor_folder — so this only touches the
named auditors, nothing else). Volume files are NOT touched; this only clears the index.

Usage (from the project root):
    python delete_supporting_chunks.py --yes
    python delete_supporting_chunks.py --yes Kaitlyn1 leander1 leander2
    python delete_supporting_chunks.py            # dry-run: report counts, delete nothing

Notes:
  * auditor_folder is matched EXACTLY (case-sensitive). The defaults below are a best guess —
    pass the exact folder names as arguments if your casing/spacing differs.
  * Destructive: pass --yes to actually delete. Without it, the script only reports what it finds.
"""

from __future__ import annotations

import os
import sys

# Make the project root importable regardless of CWD.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv()

from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger("delete_supporting_chunks")

_INDEX_KEY = "supporting"
_DEFAULT_FOLDERS = ["Kaitlyn1", "leander1", "leander2"]


def _count_for(mgr: VectorIndexManager, folder: str) -> int:
    """Best-effort count of currently-indexed chunks for an auditor_folder (dry-run reporting)."""
    try:
        return len(mgr.similarity_search(
            index_key=_INDEX_KEY, query="_", scope_id=None, top_k=1000,
            filters={"auditor_folder": folder},
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not count chunks for %r | %s", folder, exc)
        return -1


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--yes"]
    confirmed = "--yes" in sys.argv[1:]
    folders = args or _DEFAULT_FOLDERS

    settings = get_settings()
    mgr = VectorIndexManager(settings, AzureOpenAIEmbeddingProvider(settings))

    logger.info("Target index=%r | auditor_folders=%s | mode=%s",
                _INDEX_KEY, folders, "DELETE" if confirmed else "DRY-RUN")

    if not confirmed:
        for folder in folders:
            n = _count_for(mgr, folder)
            logger.info("[DRY-RUN] %r -> %s chunk(s) would be deleted",
                        folder, "unknown" if n < 0 else f"~{n}+")
        logger.info("Dry run only — no chunks deleted. Re-run with --yes to delete.")
        return

    total = 0
    for folder in folders:
        try:
            deleted = mgr.delete_by_auditor_folder(_INDEX_KEY, folder)
            total += deleted
            logger.info("Deleted %d chunk(s) for auditor_folder=%r", deleted, folder)
        except Exception as exc:  # noqa: BLE001
            logger.error("Delete failed for auditor_folder=%r | %s", folder, exc)

    logger.info("Done. Deleted %d chunk(s) across %d auditor_folder(s) from index %r.",
                total, len(folders), _INDEX_KEY)


if __name__ == "__main__":
    main()
