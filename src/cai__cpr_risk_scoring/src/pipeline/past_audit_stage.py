"""Past-audit stage — summarise past-audit reports into per-observation rows for Output E.

Sources BOTH the dedicated past_audit_index AND any past-audit reports that live in the auditor's
supporting-docs index. Supporting docs have no "past-audit" type flag, so an LLM selects which of them
are genuine past-audit reports (see past_audit_selection_prompt). Reports are grouped by source
document, deduped across the two indexes, then summarised (one row per observation).
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from src.prompts.past_audit_selection_prompt import (
    PAST_AUDIT_SELECTION_SYSTEM,
    PAST_AUDIT_SELECTION_USER,
)
from src.schemas.past_audit import PastAuditObservation
from src.utils.logger import get_logger

logger = get_logger(__name__)

_FETCH_TOP_K = 400
_DISCOVERY_QUERY = "audit observation finding issue"
_PREVIEW_CHARS = 300          # content preview per candidate shown to the selector LLM
_MAX_SUPPORTING_CANDIDATES = 80  # cap candidates sent to the selector (keeps the prompt bounded)


def _chunks_by_document(index_manager, index_key: str, auditor_id: str) -> dict[str, list]:
    """Group an index's auditor-scoped chunks by source document_name."""
    try:
        chunks = index_manager.similarity_search(
            index_key=index_key,
            query=_DISCOVERY_QUERY,
            scope_id=None,
            top_k=_FETCH_TOP_K,
            filters={"auditor_folder": auditor_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PastAudit] index read skipped | index=%s | auditor=%s | error=%s",
                       index_key, auditor_id, exc)
        return {}

    by_doc: dict[str, list] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.document_name, []).append(chunk)
    return by_doc


def _scope_period_str(settings) -> str:
    """Human-readable audit scope period from settings, or '' when not configured."""
    start = (getattr(settings, "scope_period_start", "") or "").strip()
    end = (getattr(settings, "scope_period_end", "") or "").strip()
    if start and end:
        return f"{start} to {end}"
    return start or end or ""


def _select_past_audit_docs(llm, by_doc: dict[str, list], scope_period: str = "") -> dict[str, list]:
    """Ask the LLM which supporting documents are genuine PRIOR-cycle past-audit reports; return that
    subset. The scope period is passed so the LLM excludes current-scope-period operational files."""
    if not by_doc or llm is None:
        return {}
    names = list(by_doc.keys())[:_MAX_SUPPORTING_CANDIDATES]
    candidates = "\n".join(
        f"- {name} :: {' '.join((by_doc[name][0].content or '').split())[:_PREVIEW_CHARS]}"
        for name in names
    )
    try:
        resp = llm.invoke([
            SystemMessage(content=PAST_AUDIT_SELECTION_SYSTEM),
            HumanMessage(content=PAST_AUDIT_SELECTION_USER.format(
                auditor_id="", candidates=candidates,
                scope_period=scope_period or "(not specified)")),
        ])
        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            raw = raw[4:].strip() if raw.lower().startswith("json") else raw.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        selected = json.loads(m.group(0) if m else raw).get("past_audit_documents", []) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PastAudit] supporting-doc selection failed — using none | %s", exc)
        return {}

    chosen = {name: by_doc[name] for name in selected if name in by_doc}
    logger.info("[PastAudit] supporting candidates=%d | selected as past-audit=%d",
                len(names), len(chosen))
    return chosen


def summarize_past_audits(
    index_manager, summarizer_agent, auditor_id: str, llm=None
) -> list[PastAuditObservation]:
    """One PastAuditObservation per observation across the auditor's past-audit reports, drawn from
    the past_audit_index PLUS any past-audit reports found in the supporting-docs index."""
    # Primary source: the dedicated past-audit index.
    by_doc = _chunks_by_document(index_manager, "past_audit", auditor_id)

    # Secondary source: past-audit reports that live in the supporting-docs index (LLM-selected).
    # Pass the scope period so the selector excludes current-scope-period operational files (a
    # past-audit report is a PRIOR cycle, not this period's data).
    selector_llm = llm or getattr(summarizer_agent, "_llm", None)
    scope_period = _scope_period_str(getattr(index_manager, "_settings", None))
    supporting_by_doc = _chunks_by_document(index_manager, "supporting", auditor_id)
    selected_supporting = _select_past_audit_docs(selector_llm, supporting_by_doc, scope_period)

    # Merge, deduping by normalised document name so a report in BOTH indexes is summarised once.
    seen = {re.sub(r"\s+", " ", d.lower()).strip() for d in by_doc}
    for name, chunks in selected_supporting.items():
        if re.sub(r"\s+", " ", name.lower()).strip() not in seen:
            by_doc[name] = chunks

    if not by_doc:
        logger.info("[PastAudit] No past-audit reports found for auditor=%s — skipping", auditor_id)
        return []

    logger.info("[PastAudit] Summarising | auditor=%s | reports=%d (past_audit + supporting)",
                auditor_id, len(by_doc))
    observations: list[PastAuditObservation] = []
    for position, (document_name, chunks) in enumerate(by_doc.items(), start=1):
        logger.info("[PastAudit] Summarising report %d/%d | doc=%s", position, len(by_doc), document_name)
        text = "\n".join(c.content for c in chunks if c.content)
        observations.extend(summarizer_agent.summarize(document_name, text))

    logger.info("[PastAudit] Summarised | auditor=%s | reports=%d | observations=%d",
                auditor_id, len(by_doc), len(observations))
    return observations
