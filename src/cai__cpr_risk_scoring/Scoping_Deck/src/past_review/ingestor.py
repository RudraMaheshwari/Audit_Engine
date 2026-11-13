"""Ingests classified Output E observations into the prior-review index (idempotent)."""

from __future__ import annotations

from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from .index_manager import PastReviewIndexManager
from .output_e_loader import OutputELoader

logger = get_logger(__name__)


class PastReviewIngestor:
    """Loads Output E, keeps CPR/GACS findings, and upserts them into the prior-review index."""

    def __init__(self, config: ScopingDeckConfig, index: PastReviewIndexManager) -> None:
        self._config = config
        self._index = index

    def ingest(self, force: bool = False) -> int:
        """Ingest CPR/GACS observations; return the number upserted (0 if skipped or none)."""
        auditor_id = self._config.settings.auditor_id
        self._index.ensure_index()

        if not force and self._index.is_populated(auditor_id):
            logger.info(
                "[PastReview] Index already populated for auditor=%s — skipping", auditor_id
            )
            return 0

        if not self._config.past_review_path:
            logger.warning("[PastReview] No Output E path configured — skipping ingestion")
            return 0

        try:
            observations = OutputELoader(self._config).load()
        except Exception as exc:
            logger.error("[PastReview] Failed to load Output E | %s", exc)
            return 0

        # Ingest EVERY Output E observation regardless of review kind — the index just needs to hold
        # the full past-review set (each row keeps its review_kind, CPR/GACS/OTHER, for any later use).
        if not observations:
            logger.warning("[PastReview] No observations found in Output E")
            return 0

        self._index.upsert(observations, auditor_id)
        logger.info("[PastReview] Ingested %d observation(s) from Output E", len(observations))
        return len(observations)
