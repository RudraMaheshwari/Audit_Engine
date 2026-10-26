"""Past-audit stage — summarise the past-audit index into per-observation rows for the sheet.

Reads the auditor's past-audit chunks, groups them by source document, reassembles each report's
text, and asks the summariser agent to extract observations (one row per observation).
"""

from src.schemas.past_audit import PastAuditObservation
from src.utils.logger import get_logger

logger = get_logger(__name__)

_FETCH_TOP_K = 400
_DISCOVERY_QUERY = "audit observation finding issue"


def _chunks_by_document(index_manager, auditor_id: str) -> dict[str, list]:
    try:
        chunks = index_manager.similarity_search(
            index_key="past_audit",
            query=_DISCOVERY_QUERY,
            scope_id=None,
            top_k=_FETCH_TOP_K,
            filters={"auditor_folder": auditor_id},
        )
    except Exception as exc:
        logger.warning("[PastAudit] index read skipped | auditor=%s | error=%s", auditor_id, exc)
        return {}

    by_doc: dict[str, list] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.document_name, []).append(chunk)
    return by_doc


def summarize_past_audits(
    index_manager, summarizer_agent, auditor_id: str
) -> list[PastAuditObservation]:
    """Return one PastAuditObservation per observation across the auditor's past-audit reports."""
    by_doc = _chunks_by_document(index_manager, auditor_id)
    if not by_doc:
        logger.info("[PastAudit] No past-audit reports found for auditor=%s — skipping", auditor_id)
        return []

    logger.info("[PastAudit] Summarising | auditor=%s | reports=%d", auditor_id, len(by_doc))
    observations: list[PastAuditObservation] = []
    for position, (document_name, chunks) in enumerate(by_doc.items(), start=1):
        logger.info(
            "[PastAudit] Summarising report %d/%d | doc=%s",
            position, len(by_doc), document_name,
        )
        text = "\n".join(c.content for c in chunks if c.content)
        observations.extend(summarizer_agent.summarize(document_name, text))

    logger.info(
        "[PastAudit] Summarised | auditor=%s | reports=%d | observations=%d",
        auditor_id, len(by_doc), len(observations),
    )
    return observations
