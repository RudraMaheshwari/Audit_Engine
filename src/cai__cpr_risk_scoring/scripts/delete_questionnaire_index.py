
"""Delete all questionnaire-index rows for one auditor.

The questionnaire index uses auditor_id, not auditor_folder.

This script queries the questionnaire index, deletes matching rows by
primary key, and repeatedly verifies that no matching rows remain.

Run from the project root or scripts/ directory with the venv active
and .env configured:

    python scripts/delete_questionnaire_index.py --auditor Naeem_1
    python scripts/delete_questionnaire_index.py --auditor Naeem_1 --dry-run

The --auditor flag overrides AUDITOR_ID, which overrides the default.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

# Make src importable and load the project-root .env.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(
    (_ROOT / ".env")
    if (_ROOT / ".env").exists()
    else find_dotenv(usecwd=True)
)

from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.embeddings.azure_openai_provider import (  # noqa: E402
    AzureOpenAIEmbeddingProvider,
)
from src.utils.logger import get_logger  # noqa: E402
from src.vector_store.index_manager import VectorIndexManager  # noqa: E402

logger = get_logger("delete_questionnaire_index")

_DEFAULT_AUDITOR_ID = "Naeem_1"
_PROBE_EPSILON = 1e-6

_SCAN_BATCH = 1000
_COUNT_CAP = 10000

_ROUND_PAUSE = 3.0
_MAX_ROUNDS = 2000
_MAX_STAGNANT_ROUNDS = 10

_RETRY_ATTEMPTS = 10
_RETRY_BASE_S = 5.0
_RETRY_MAX_S = 90.0


def _questionnaire_index(mgr: VectorIndexManager):
    """Open the configured questionnaire direct-access index."""
    return mgr._client.get_index(
        endpoint_name=mgr._settings.databricks_vector_search_endpoint,
        index_name=mgr._index_names["questionnaire"],
    )


def _retry(fn, what: str):
    """Retry failed operations with exponential backoff and jitter."""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return fn()

        except Exception as exc:  # noqa: BLE001
            if attempt == _RETRY_ATTEMPTS:
                raise

            wait = min(
                _RETRY_MAX_S,
                _RETRY_BASE_S * (2 ** (attempt - 1)),
            ) + random.uniform(0, 2)

            logger.warning(
                "[DeleteQuestionnaireIndex] %s failed "
                "(attempt %d/%d); waiting %.0fs | %s",
                what,
                attempt,
                _RETRY_ATTEMPTS,
                wait,
                str(exc)[:250],
            )
            time.sleep(wait)


def _rows_for(
    index,
    auditor_id: str,
    dim: int,
    num_results: int,
) -> list[str]:
    """Retrieve unique chunk IDs and verify auditor_id for each row."""
    response = _retry(
        lambda: index.similarity_search(
            columns=["chunk_id", "auditor_id"],
            query_vector=[_PROBE_EPSILON] * dim,
            filters={"auditor_id": auditor_id},
            num_results=num_results,
        ),
        "query",
    )

    if not isinstance(response, dict):
        raise RuntimeError(
            f"Unexpected similarity_search response: "
            f"{type(response).__name__}"
        )

    result = response.get("result") or {}
    rows = result.get("data_array") or []

    ids = []
    seen = set()

    for row in rows:
        if not row or len(row) < 2:
            logger.warning(
                "[DeleteQuestionnaireIndex] Skipping malformed row: %r",
                row,
            )
            continue

        chunk_id, row_auditor_id = row[0], row[1]

        # Defense in depth: never delete a row for a different auditor.
        if row_auditor_id != auditor_id:
            continue

        if chunk_id is None or str(chunk_id) == "":
            logger.warning(
                "[DeleteQuestionnaireIndex] Matching row has no chunk_id"
            )
            continue

        chunk_id = str(chunk_id)

        if chunk_id not in seen:
            ids.append(chunk_id)
            seen.add(chunk_id)

    return ids


def _delete_batch(index, ids: list[str]) -> None:
    """Delete IDs through the direct-access index API."""
    if not ids:
        return

    response = _retry(
        lambda: index.delete(primary_keys=ids),
        "delete",
    )

    logger.info(
        "[DeleteQuestionnaireIndex] Delete request returned | "
        "requested_ids=%d | response=%s",
        len(ids),
        str(response)[:500],
    )


def _scan_count(
    index,
    auditor_id: str,
    dim: int,
    num_results: int,
) -> tuple[list[str], bool]:
    """Return matching IDs and whether the query may have hit its limit."""
    ids = _rows_for(
        index=index,
        auditor_id=auditor_id,
        dim=dim,
        num_results=num_results,
    )

    # Similarity search returns ranked results, not a guaranteed full scan.
    # A full page means additional rows may exist.
    possibly_capped = len(ids) >= num_results
    return ids, possibly_capped


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete one auditor's rows from the questionnaire "
            "vector index and verify the result."
        )
    )

    parser.add_argument(
        "--auditor",
        "--auditor-id",
        dest="auditor",
        default=os.environ.get(
            "AUDITOR_ID",
            _DEFAULT_AUDITOR_ID,
        ),
        help=f"auditor_id to delete (default: {_DEFAULT_AUDITOR_ID})",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report matching rows without deleting anything.",
    )

    args = parser.parse_args()
    auditor_id = (args.auditor or "").strip()

    if not auditor_id:
        logger.error(
            "[DeleteQuestionnaireIndex] --auditor is required"
        )
        return 2

    try:
        settings = get_settings()
        dim = settings.embedding_dimensions

        mgr = VectorIndexManager(
            settings,
            AzureOpenAIEmbeddingProvider(settings),
        )

        index = _questionnaire_index(mgr)

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[DeleteQuestionnaireIndex] Could not initialize "
            "questionnaire index: %s",
            exc,
        )
        return 1

    logger.info(
        "[DeleteQuestionnaireIndex] Start | auditor_id=%s | "
        "index=%s",
        auditor_id,
        mgr._index_names["questionnaire"],
    )

    # Initial query.
    try:
        initial_ids, possibly_capped = _scan_count(
            index,
            auditor_id,
            dim,
            _COUNT_CAP if args.dry_run else _SCAN_BATCH,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[DeleteQuestionnaireIndex] Initial query failed: %s",
            exc,
        )
        return 1

    if args.dry_run:
        suffix = (
            " | RESULT MAY BE CAPPED; MORE ROWS MAY EXIST"
            if possibly_capped
            else ""
        )

        logger.info(
            "[DeleteQuestionnaireIndex] DRY-RUN | auditor_id=%s "
            "| matching_rows_returned=%d%s | nothing deleted",
            auditor_id,
            len(initial_ids),
            suffix,
        )
        return 0

    logger.info(
        "[DeleteQuestionnaireIndex] Initial matching IDs returned: %d",
        len(initial_ids),
    )

    if not initial_ids:
        logger.info(
            "[DeleteQuestionnaireIndex] No matching rows returned "
            "by the initial query."
        )
        return 0

    total_requested = 0
    previous_remaining: set[str] | None = None
    stagnant_rounds = 0

    try:
        for round_no in range(1, _MAX_ROUNDS + 1):
            # Always query again. Do not exclude IDs from previous rounds:
            # they may still exist if the delete request did not take effect.
            ids = _rows_for(
                index,
                auditor_id,
                dim,
                _SCAN_BATCH,
            )

            remaining = set(ids)

            logger.info(
                "[DeleteQuestionnaireIndex] Round %d/%d | "
                "matching_rows_returned=%d",
                round_no,
                _MAX_ROUNDS,
                len(ids),
            )

            # Only a fresh empty result allows a clean status.
            if not ids:
                logger.info(
                    "[DeleteQuestionnaireIndex] VERIFIED CLEAN | "
                    "auditor_id=%s | total_requested_for_deletion=%d",
                    auditor_id,
                    total_requested,
                )
                return 0

            if previous_remaining is not None:
                if remaining == previous_remaining:
                    stagnant_rounds += 1
                else:
                    stagnant_rounds = 0

            previous_remaining = remaining

            if stagnant_rounds >= _MAX_STAGNANT_ROUNDS:
                logger.error(
                    "[DeleteQuestionnaireIndex] STOPPING: no progress "
                    "for %d consecutive rounds. The query is still "
                    "returning matching rows. Sample IDs: %s",
                    stagnant_rounds,
                    ids[:20],
                )
                return 1

            # Delete all IDs from this query in one request.
            _delete_batch(index, ids)
            total_requested += len(ids)

            logger.info(
                "[DeleteQuestionnaireIndex] Delete submitted | "
                "round=%d | batch_size=%d | total_requested=%d",
                round_no,
                len(ids),
                total_requested,
            )

            time.sleep(_ROUND_PAUSE)

            # Explicit verification after every delete request.
            verified_ids = _rows_for(
                index,
                auditor_id,
                dim,
                _SCAN_BATCH,
            )

            logger.info(
                "[DeleteQuestionnaireIndex] Verification | "
                "round=%d | matching_rows_remaining=%d",
                round_no,
                len(verified_ids),
            )

            if not verified_ids:
                logger.info(
                    "[DeleteQuestionnaireIndex] VERIFIED CLEAN | "
                    "auditor_id=%s | total_requested_for_deletion=%d",
                    auditor_id,
                    total_requested,
                )
                return 0

            if set(verified_ids) == remaining:
                logger.warning(
                    "[DeleteQuestionnaireIndex] No visible change "
                    "after deletion. Check primary-key correctness, "
                    "index type, API response, and source-table sync."
                )

        # Safety cap reached. Do not report success without verification.
        final_ids = _rows_for(
            index,
            auditor_id,
            dim,
            _SCAN_BATCH,
        )

        if final_ids:
            logger.error(
                "[DeleteQuestionnaireIndex] VERIFICATION FAILED | "
                "round_limit=%d | matching_rows_remaining=%d | "
                "sample_ids=%s",
                _MAX_ROUNDS,
                len(final_ids),
                final_ids[:20],
            )
            return 1

        logger.info(
            "[DeleteQuestionnaireIndex] VERIFIED CLEAN | "
            "auditor_id=%s | total_requested_for_deletion=%d",
            auditor_id,
            total_requested,
        )
        return 0

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[DeleteQuestionnaireIndex] FAILED | auditor_id=%s | "
            "total_requested=%d | error=%s",
            auditor_id,
            total_requested,
            exc,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
