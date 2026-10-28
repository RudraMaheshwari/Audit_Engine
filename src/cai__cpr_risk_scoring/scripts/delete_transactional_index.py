"""Delete all chunks for one auditor from a Databricks Vector Search index.

Scans the index using the control-plane scan_index API, identifies records
by auditor_folder, deletes using the index's actual primary key, and verifies
that no matching records remain.

Examples:
    python scripts/delete_transactional_index.py --auditor Naeem_1
    python scripts/delete_transactional_index.py --auditor Naeem_1 --index-key raw_transactional
    python scripts/delete_transactional_index.py --auditor Naeem_1 --dry-run

CLI arguments override environment variables, which override defaults.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make the project root importable when run from the project root or scripts/.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(
    (_ROOT / ".env")
    if (_ROOT / ".env").exists()
    else find_dotenv(usecwd=True)
)

from databricks.sdk import WorkspaceClient  # noqa: E402

from src.config.databricks_auth import build_config  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("delete_transactional_index")

_DEFAULT_AUDITOR_FOLDER = "Naeem_1"
_DEFAULT_INDEX_KEY = "transactional_analysis"

# scan_index has NO server-side filter and NO column projection, so the whole index is read to find
# one auditor and every row carries its content + embedding. Big pages mean fewer requests, but too
# big overflows the response size limit — so we START high and AUTO-SHRINK (halve) on "too large"
# until the page fits, then keep that size. The settled size is still far better than tiny pages.
_SCAN_PAGE_START = 1000
_SCAN_PAGE_MIN = 20
_scan_page = {"size": _SCAN_PAGE_START}  # mutable: shrinks once and stays shrunk for the whole run
_DELETE_BATCH = 100
_RETRY_ATTEMPTS = 5
_RETRY_BASE_S = 3.0

# Maximum complete scan/delete/verify passes.
_MAX_DELETE_PASSES = 10

# Wait for the index to reflect deletion before verifying again.
_VERIFY_WAIT_S = 2.0

_INDEX_KEY_TO_ATTR = {
    "transactional_analysis": "transactional_analysis_index_name",
    "raw_transactional": "raw_transactional_tables_index_name",
    "supporting": "supporting_docs_index_name",
    "past_audit": "past_audit_index_name",
    "si_history": "si_history_index_name",
    "legal": "legal_index_name",
    "additional_input": "additional_input_index_name",
    "local_policies": "local_policies_index_name",
}


def _index_name(settings, index_key: str) -> str | None:
    """Build the fully qualified index name."""
    attr = _INDEX_KEY_TO_ATTR.get(index_key)
    if not attr:
        return None

    configured_name = getattr(settings, attr, None)
    if not configured_name:
        raise ValueError(
            f"Setting {attr!r} is empty for index key {index_key!r}"
        )

    return (
        f"{settings.databricks_catalog}."
        f"{settings.databricks_schema_name}."
        f"{configured_name}"
    )


def _response_dict(response) -> dict:
    """Normalize API responses that may be dictionaries or SDK objects."""
    if isinstance(response, dict):
        return response

    if hasattr(response, "as_dict"):
        result = response.as_dict()
        if isinstance(result, dict):
            return result

    raise RuntimeError(
        f"Unexpected API response type: {type(response).__name__}"
    )


def _get_primary_key(w: WorkspaceClient, index_name: str) -> str:
    """Retrieve the index's primary key from index metadata."""
    path = f"/api/2.0/vector-search/indexes/{index_name}"
    metadata = _response_dict(w.api_client.do("GET", path))

    primary_key = metadata.get("primary_key")

    if not primary_key:
        raise RuntimeError(
            f"Could not determine primary_key from index metadata. "
            f"Response keys: {list(metadata.keys())}. "
            "Inspect the metadata response and adjust this function "
            "for your Databricks API/SDK version."
        )

    logger.info(
        "[DeleteTxnIndex] Index primary key: %s",
        primary_key,
    )
    return str(primary_key)


def _scan_once(w: WorkspaceClient, index_name: str, last_pk):
    """One scan_index page, auto-shrinking the page size on 'response too large' and retrying."""
    while True:
        size = _scan_page["size"]
        try:
            return w.vector_search_indexes.scan_index(
                index_name=index_name,
                num_results=size,
                last_primary_key=last_pk,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            too_large = "too large" in msg or "reduce number of results" in msg
            if too_large and size > _SCAN_PAGE_MIN:
                _scan_page["size"] = max(_SCAN_PAGE_MIN, size // 2)
                logger.warning(
                    "[DeleteTxnIndex] Response too large at page=%d — shrinking to %d and retrying",
                    size, _scan_page["size"],
                )
                continue
            raise


def _field_value(field):
    """Extract a supported scalar value from a scan field."""
    value = getattr(field, "value", None)

    if value is None:
        return None

    for attr in (
        "string_value",
        "number_value",
        "bool_value",
    ):
        result = getattr(value, attr, None)
        if result is not None:
            return str(result)

    # Accommodate dictionary-shaped fields if the SDK returns them.
    if isinstance(value, dict):
        for attr in (
            "string_value",
            "number_value",
            "bool_value",
        ):
            result = value.get(attr)
            if result is not None:
                return str(result)

    return None


def _scan_keys(
    w: WorkspaceClient,
    index_name: str,
    auditor_folder: str,
    primary_key: str,
) -> list[str]:
    """Scan every page and return primary keys for the target auditor."""
    keys: list[str] = []
    seen: set[str] = set()
    last_pk = None
    pages = 0

    while True:
        response = _scan_once(w, index_name, last_pk)

        rows = response.data or []
        if not rows:
            break

        for row in rows:
            values = {
                field.key: _field_value(field)
                for field in (row.fields or [])
            }

            if values.get("auditor_folder") != auditor_folder:
                continue

            pk = values.get(primary_key)

            if pk is None:
                raise RuntimeError(
                    f"Matching record has no value for primary key "
                    f"{primary_key!r}. Available fields: "
                    f"{list(values.keys())}. Ensure scan_index returns "
                    "the primary-key column."
                )

            if pk not in seen:
                keys.append(pk)
                seen.add(pk)

        pages += 1
        next_pk = response.last_primary_key

        if pages % 50 == 0:
            logger.info(
                "[DeleteTxnIndex] Scanned %d pages; matches=%d",
                pages,
                len(keys),
            )

        if not next_pk or next_pk == last_pk:
            break

        last_pk = next_pk

    logger.info(
        "[DeleteTxnIndex] Scan complete | pages=%d | matches=%d",
        pages,
        len(keys),
    )
    return keys


def _delete_batch(
    w: WorkspaceClient,
    index_name: str,
    batch: list[str],
) -> int:
    """Delete a batch and inspect the returned API result."""
    path = f"/api/2.0/vector-search/indexes/{index_name}/delete-data"

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            response = w.api_client.do(
                "DELETE",
                path,
                body={"primary_keys": batch},
            )

            result = _response_dict(response)
            status = result.get("status")
            success_count = result.get("success_row_count", 0)
            failed_keys = result.get("failed_primary_keys") or []

            # Some API versions may wrap the delete result.
            nested = result.get("result")
            if isinstance(nested, dict):
                status = status or nested.get("status")
                success_count = nested.get(
                    "success_row_count",
                    success_count,
                )
                failed_keys = nested.get(
                    "failed_primary_keys",
                    failed_keys,
                ) or []

            logger.info(
                "[DeleteTxnIndex] Delete response | status=%s "
                "| requested=%d | reported_success=%s "
                "| failed=%d",
                status,
                len(batch),
                success_count,
                len(failed_keys),
            )

            if failed_keys:
                raise RuntimeError(
                    f"API reported failed primary keys: {failed_keys[:10]}"
                )

            if status and str(status).upper() not in (
                "SUCCESS",
                "SUCCEEDED",
            ):
                raise RuntimeError(
                    f"Delete API returned status={status!r}"
                )

            if success_count is not None:
                return int(success_count)

            # The endpoint may return an empty response on some versions.
            # The subsequent scan, not this return value, verifies deletion.
            logger.warning(
                "[DeleteTxnIndex] API did not report a success count; "
                "the next scan will determine the actual outcome."
            )
            return 0

        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS:
                raise

            wait = _RETRY_BASE_S * attempt
            logger.warning(
                "[DeleteTxnIndex] Delete failed (attempt %d/%d); "
                "retrying in %.0fs | %s",
                attempt,
                _RETRY_ATTEMPTS,
                wait,
                str(exc)[:250],
            )
            time.sleep(wait)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete all chunks for one auditor from a "
            "Databricks Vector Search index."
        )
    )

    parser.add_argument(
        "--auditor",
        "--auditor-folder",
        dest="auditor",
        default=os.environ.get(
            "AUDITOR_FOLDER",
            _DEFAULT_AUDITOR_FOLDER,
        ),
        help=f"auditor_folder to delete (default: {_DEFAULT_AUDITOR_FOLDER})",
    )

    parser.add_argument(
        "--index-key",
        default=os.environ.get(
            "INDEX_KEY",
            _DEFAULT_INDEX_KEY,
        ),
        help=(
            f"Target index (default: {_DEFAULT_INDEX_KEY}). "
            f"Valid keys: {', '.join(sorted(_INDEX_KEY_TO_ATTR))}"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count matching records without deleting anything.",
    )

    args = parser.parse_args()

    auditor_folder = (args.auditor or "").strip()
    index_key = (args.index_key or "").strip()

    if not auditor_folder:
        logger.error("[DeleteTxnIndex] --auditor is required")
        return 2

    if index_key not in _INDEX_KEY_TO_ATTR:
        logger.error(
            "[DeleteTxnIndex] Unknown index key %r. Valid keys: %s",
            index_key,
            ", ".join(sorted(_INDEX_KEY_TO_ATTR)),
        )
        return 2

    try:
        settings = get_settings()
        index_name = _index_name(settings, index_key)
        if not index_name:
            logger.error(
                "[DeleteTxnIndex] Could not resolve index %r",
                index_key,
            )
            return 2

        w = WorkspaceClient(config=build_config(settings))

    except Exception as exc:
        logger.exception(
            "[DeleteTxnIndex] Configuration/authentication failed: %s",
            exc,
        )
        return 1

    logger.info(
        "[DeleteTxnIndex] Starting | index=%s | auditor_folder=%s",
        index_name,
        auditor_folder,
    )

    try:
        primary_key = _get_primary_key(w, index_name)

        initial_keys = _scan_keys(
            w,
            index_name,
            auditor_folder,
            primary_key,
        )

    except Exception as exc:
        logger.exception(
            "[DeleteTxnIndex] Initial scan failed: %s",
            exc,
        )
        return 1

    logger.info(
        "[DeleteTxnIndex] Initial matching records: %d",
        len(initial_keys),
    )

    if args.dry_run:
        logger.info(
            "[DeleteTxnIndex] DRY RUN: no records were deleted."
        )
        return 0

    if not initial_keys:
        logger.info(
            "[DeleteTxnIndex] No matching records found."
        )
        return 0

    reported_deleted = 0
    keys = initial_keys  # reuse the scan already done above; re-scan only to VERIFY each pass

    try:
        for pass_no in range(1, _MAX_DELETE_PASSES + 1):
            if not keys:
                logger.info(
                    "[DeleteTxnIndex] VERIFIED CLEAN | index=%s "
                    "| auditor_folder=%s | reported_deleted=%d",
                    index_name,
                    auditor_folder,
                    reported_deleted,
                )
                return 0

            before_count = len(keys)
            logger.info(
                "[DeleteTxnIndex] Pass %d/%d | deleting=%d",
                pass_no,
                _MAX_DELETE_PASSES,
                before_count,
            )

            for offset in range(0, len(keys), _DELETE_BATCH):
                batch = keys[offset : offset + _DELETE_BATCH]

                count = _delete_batch(
                    w,
                    index_name,
                    batch,
                )
                reported_deleted += count

                logger.info(
                    "[DeleteTxnIndex] Batch submitted | "
                    "batch_size=%d | reported_success=%d",
                    len(batch),
                    count,
                )

            time.sleep(_VERIFY_WAIT_S)

            # Re-scan to verify; the result becomes the next pass's work list.
            keys = _scan_keys(
                w,
                index_name,
                auditor_folder,
                primary_key,
            )

            logger.info(
                "[DeleteTxnIndex] Verification | before=%d "
                "| remaining=%d",
                before_count,
                len(keys),
            )

            if keys and len(keys) >= before_count:
                logger.warning(
                    "[DeleteTxnIndex] No measurable progress. "
                    "Remaining primary keys sample: %s",
                    keys[:10],
                )

        if keys:
            logger.error(
                "[DeleteTxnIndex] VERIFICATION FAILED after %d passes "
                "| remaining=%d | sample_keys=%s",
                _MAX_DELETE_PASSES,
                len(keys),
                keys[:20],
            )
            return 1

        logger.info(
            "[DeleteTxnIndex] VERIFIED CLEAN | index=%s "
            "| auditor_folder=%s | reported_deleted=%d",
            index_name,
            auditor_folder,
            reported_deleted,
        )
        return 0

    except Exception as exc:
        logger.exception(
            "[DeleteTxnIndex] Deletion/verification failed: %s",
            exc,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
