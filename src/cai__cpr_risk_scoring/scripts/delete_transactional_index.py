"""Delete ALL chunks for one auditor from a per-auditor vector index (default: transactional_analysis).

Enumerates rows with `scan_index` and deletes by primary key via the workspace CONTROL-PLANE API —
NOT the data-plane similarity_search path, which is heavily 429-throttled for bulk scans/deletes.
Filters on `auditor_folder` (the label these indexes carry). Self-contained (SP auth), self-terminating,
idempotent.

Run (from the project root OR scripts/, venv active, .env configured):
    python scripts/delete_transactional_index.py --auditor Naeem_1
    python scripts/delete_transactional_index.py --auditor Naeem_1 --index-key raw_transactional
    python scripts/delete_transactional_index.py --auditor Naeem_1 --dry-run   # count only, no delete

Flags override env (AUDITOR_FOLDER / INDEX_KEY), which override the defaults below.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make `src` importable and load the project-root .env when run directly from anywhere (e.g. scripts/).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))

from databricks.sdk import WorkspaceClient  # noqa: E402

from src.config.databricks_auth import build_config  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("delete_transactional_index")

_DEFAULT_AUDITOR_FOLDER = "Naeem_1"
_DEFAULT_INDEX_KEY = "transactional_analysis"
_SCAN_PAGE = 50          # rows per scan_index page (cursor pagination)
_DELETE_BATCH = 100      # primary keys per delete call
_RETRY_ATTEMPTS = 5      # retries per delete call
_RETRY_BASE_S = 3.0      # backoff seconds × attempt (3, 6, 9, …)

# index-key -> the Settings attribute holding that index's (unqualified) name. Mirrors
# VectorIndexManager._index_names so the FQN is resolved identically.
_INDEX_KEY_TO_ATTR = {
    "transactional_analysis": "transactional_analysis_index_name",
    "raw_transactional":      "raw_transactional_tables_index_name",
    "supporting":             "supporting_docs_index_name",
    "past_audit":             "past_audit_index_name",
    "si_history":             "si_history_index_name",
    "legal":                  "legal_index_name",
    "additional_input":       "additional_input_index_name",
    "local_policies":         "local_policies_index_name",
}


def _index_name(settings, index_key: str) -> str | None:
    """Fully-qualified index name for an index key, built the same way VectorIndexManager does."""
    attr = _INDEX_KEY_TO_ATTR.get(index_key)
    if not attr:
        return None
    return f"{settings.databricks_catalog}.{settings.databricks_schema_name}.{getattr(settings, attr)}"


def _scan_keys(w: WorkspaceClient, index_name: str, auditor_folder: str) -> list[str]:
    """chunk_ids of rows whose auditor_folder matches, via cursor-paginated scan_index (control plane)."""
    keys: list[str] = []
    last_pk = None
    pages = 0
    while True:
        res = w.vector_search_indexes.scan_index(
            index_name=index_name, num_results=_SCAN_PAGE, last_primary_key=last_pk
        )
        data = res.data or []
        if not data:
            break
        for row in data:
            vals = {f.key: f.value.string_value for f in row.fields}
            if vals.get("auditor_folder") == auditor_folder and vals.get("chunk_id"):
                keys.append(vals["chunk_id"])
        pages += 1
        last_pk = res.last_primary_key
        if not last_pk:
            break
        if pages % 50 == 0:
            logger.info("[DeleteTxnIndex] scanned %d page(s) | matched so far=%d", pages, len(keys))
    return keys


def _delete_batch(w: WorkspaceClient, index_name: str, batch: list[str]) -> None:
    """Delete a batch of primary keys via the control-plane delete-data endpoint, with light retry."""
    path = f"/api/2.0/vector-search/indexes/{index_name}/delete-data"
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            w.api_client.do("DELETE", path, query={"primary_keys": batch})
            return
        except Exception as exc:  # noqa: BLE001
            if attempt == _RETRY_ATTEMPTS:
                raise
            wait = _RETRY_BASE_S * attempt
            logger.warning("[DeleteTxnIndex] delete failed (attempt %d/%d) — waiting %.0fs | %s",
                           attempt, _RETRY_ATTEMPTS, wait, str(exc)[:160])
            time.sleep(wait)


def main() -> int:
    p = argparse.ArgumentParser(description="Delete one auditor's chunks from a per-auditor vector index.")
    p.add_argument("--auditor", "--auditor-folder", dest="auditor",
                   default=os.environ.get("AUDITOR_FOLDER", _DEFAULT_AUDITOR_FOLDER),
                   help=f"auditor_folder to delete (default: {_DEFAULT_AUDITOR_FOLDER})")
    p.add_argument("--index-key", dest="index_key",
                   default=os.environ.get("INDEX_KEY", _DEFAULT_INDEX_KEY),
                   help=f"per-auditor index to target (default: {_DEFAULT_INDEX_KEY}). "
                        f"One of: {', '.join(sorted(_INDEX_KEY_TO_ATTR))}")
    p.add_argument("--dry-run", action="store_true", help="report the row count; delete nothing")
    args = p.parse_args()

    auditor_folder = (args.auditor or "").strip()
    index_key = (args.index_key or "").strip()
    if not auditor_folder:
        logger.error("[DeleteTxnIndex] --auditor is required")
        return 2

    settings = get_settings()
    index_name = _index_name(settings, index_key)
    if not index_name:
        logger.error("[DeleteTxnIndex] Unknown --index-key %r | valid: %s",
                     index_key, ", ".join(sorted(_INDEX_KEY_TO_ATTR)))
        return 2

    try:
        w = WorkspaceClient(config=build_config(settings))
    except Exception as exc:  # noqa: BLE001
        logger.error("[DeleteTxnIndex] Could not authenticate to the workspace | %s", exc)
        return 1

    logger.info("[DeleteTxnIndex] Scanning | index=%s | auditor_folder=%s", index_name, auditor_folder)
    try:
        keys = _scan_keys(w, index_name, auditor_folder)
    except Exception as exc:  # noqa: BLE001
        logger.error("[DeleteTxnIndex] Scan failed | index=%s | auditor_folder=%s | %s",
                     index_name, auditor_folder, exc)
        return 1

    logger.info("[DeleteTxnIndex] Found %d chunk(s) for auditor_folder=%s", len(keys), auditor_folder)
    if args.dry_run:
        logger.info("[DeleteTxnIndex] DRY-RUN — nothing deleted (re-run without --dry-run to delete).")
        return 0
    if not keys:
        logger.info("[DeleteTxnIndex] Nothing to delete — index is already clean for this auditor.")
        return 0

    deleted = 0
    try:
        for i in range(0, len(keys), _DELETE_BATCH):
            batch = keys[i:i + _DELETE_BATCH]
            _delete_batch(w, index_name, batch)
            deleted += len(batch)
            logger.info("[DeleteTxnIndex] Deleted %d/%d | auditor_folder=%s", deleted, len(keys), auditor_folder)
    except Exception as exc:  # noqa: BLE001
        logger.error("[DeleteTxnIndex] FAILED after %d chunk(s) | auditor_folder=%s | %s",
                     deleted, auditor_folder, exc)
        return 1

    logger.info("[DeleteTxnIndex] DONE | deleted %d chunk(s) | index=%s | auditor_folder=%s",
                deleted, index_name, auditor_folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
