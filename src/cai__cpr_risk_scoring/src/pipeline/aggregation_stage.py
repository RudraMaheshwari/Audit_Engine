"""Aggregation stage — turn per-question and transactional findings into risk-centric output.

Runs after the per-question loop: builds raw findings, clusters them into unique risks via the
aggregation agent, sets Past Occurrence per risk via the (per-auditor) knowledge retriever, and
computes the scope-level Overall Risk Category (average) for the Risk Summary.
"""

from src.config.constants import (
    MANUAL_REVIEW_LABEL,
    MATRIX_BAND_HIGH_MIN,
    MATRIX_BAND_MEDIUM_MIN,
    OBS_ORIGIN_AGGREGATED,
    RISK_CATEGORY_HIGH,
    RISK_CATEGORY_LOW,
    RISK_CATEGORY_MEDIUM,
    RISK_POTENTIAL,
)

# A transactional finding enters Output C when it carries a real matrix band.
_BANDED_CATEGORIES = {
    RISK_CATEGORY_HIGH.lower(),
    RISK_CATEGORY_MEDIUM.lower(),
    RISK_CATEGORY_LOW.lower(),
}
from src.knowledge_base import KnowledgeRetriever
from src.models.schemas import OutputRow
from src.schemas.aggregation import (
    AggregatedRisk,
    raw_finding_from_output_row,
    raw_finding_from_risk_output,
)
from src.schemas.state import RiskOutput
from src.scoring import average_category
from src.utils.logger import get_logger

logger = get_logger(__name__)

_RECURRENCE_TOP_K = 3


def _is_reportable(row: OutputRow) -> bool:
    # Output C is the genuine, categorised risk view. Only confirmed Potential Risk findings are
    # clustered here. Further Review Required (uncategorised open items) and No Risk are excluded —
    # FRR appears only in the per-question sheet, never in the aggregated risk view.
    # NOTE: on OutputRow the classification type lives in `risk_title`; `risk_type` is a short summary.
    category = (row.risk_category or "").strip().lower()
    title = (row.risk_title or "").strip()
    manual = MANUAL_REVIEW_LABEL.lower()
    if manual in title.lower() or manual in category:
        return False
    return title.lower() == RISK_POTENTIAL.lower()


def _is_reportable_transactional(finding: RiskOutput) -> bool:
    # A transactional finding enters Output C when it carries a real matrix band (High/Medium/Low) —
    # this includes Potential Risk AND banded Further Review (Medium/Low), since Output C is
    # category-based (it never shows the PR/FRR title). No Risk / Not Applicable / blank are excluded.
    # The category is derived on the shared L x I matrix by the transactional agent.
    return (finding.risk_category or "").strip().lower() in _BANDED_CATEGORIES


def build_raw_findings(output_rows: list[OutputRow], transactional: list[RiskOutput]):
    """Adapt reportable per-question rows + reportable transactional findings into raw findings.

    Questionnaire side: only confirmed Potential Risk clusters into Output C (questionnaire FRR stays
    on the per-question sheet as an uncategorised open item). Transactional side: any finding with a
    real matrix band (High/Medium/Low) clusters in — this includes banded FRR (Medium/Low), which have
    no per-question sheet and would otherwise be lost. No Risk / Not Applicable are excluded from both.
    """
    raws = [raw_finding_from_output_row(r) for r in output_rows if _is_reportable(r)]
    raws += [raw_finding_from_risk_output(f) for f in transactional if _is_reportable_transactional(f)]
    return raws


# Minimum similarity for a historical hit to count as a genuine past occurrence. Without this, ANY
# loose vector neighbour in the past-audit/SI/legal indexes flipped Past Occurrence to "Yes"; the gate
# keeps it strict (same bar as corroboration below).
_RECURRENCE_MIN_SIMILARITY = 0.60


def _apply_recurrence(risk: AggregatedRisk, retriever: KnowledgeRetriever) -> None:
    query = f"{risk.risk_type} {risk.risk_description}".strip()
    hits = retriever.recurrence_for(query, top_k=_RECURRENCE_TOP_K)
    # Strict: only a genuinely-similar hit counts. Keep hits whose similarity clears the threshold
    # (a missing score is treated as qualifying, as the retriever does not always populate it), then
    # attribute the flag to the single best-matching historical document.
    qualifying = [
        h for h in hits
        if h.similarity_score is None or h.similarity_score >= _RECURRENCE_MIN_SIMILARITY
    ]
    if qualifying:
        best = max(qualifying, key=lambda h: h.similarity_score if h.similarity_score is not None else 1.0)
        risk.past_occurrence = True
        risk.past_occurrence_link = best.document_name


# Minimum top-hit similarity for a source index to count as corroborating this risk type. A source
# NEVER originates a risk — it only annotates a risk the system independently identified (G11/GATE D).
_CORROBORATION_MIN_SIMILARITY = 0.60


def _corroborates(hits, min_sim: float = _CORROBORATION_MIN_SIMILARITY) -> bool:
    """True when the source's best hit is relevant enough to count as corroborating this risk type."""
    if not hits:
        return False
    top = hits[0].similarity_score
    return top is None or top >= min_sim


def _apply_corroboration(risk: AggregatedRisk, retriever: KnowledgeRetriever) -> None:
    """Annotate the Risk Description when this risk's type is ALSO reflected in the source indexes
    (SI History, Legal, Additional Input, Past Audit, Management responses). Corroboration only — never creates or re-bands a risk.
    """
    query = f"{risk.risk_type} {risk.scope_area} {risk.risk_description}".strip()
    sources: list[str] = []
    if _corroborates(retriever.si_history_for(query, top_k=_RECURRENCE_TOP_K)):
        sources.append("SI History")
    if _corroborates(retriever.legal_for(query, top_k=_RECURRENCE_TOP_K)):
        sources.append("Legal")
    if _corroborates(retriever.additional_input_for(query, top_k=_RECURRENCE_TOP_K)):
        sources.append("Additional Input")
    if _corroborates(retriever.past_audit_for(query, top_k=_RECURRENCE_TOP_K)):
        sources.append("prior audit findings")
    if _corroborates(retriever.questionnaire_for(query, top_k=_RECURRENCE_TOP_K)):
        sources.append("management responses (questionnaire)")
    if not sources:
        return
    note = "This risk type is also reflected in: " + "; ".join(sources) + " (corroborating context)."
    if note not in (risk.risk_description or ""):
        risk.risk_description = f"{risk.risk_description}\n- {note}".strip()


def _scope_overall(risks: list[AggregatedRisk]) -> dict:
    by_scope: dict[str, list[str]] = {}
    for risk in risks:
        by_scope.setdefault(risk.scope_area, []).append(risk.risk_category)
    return {scope: average_category(cats) for scope, cats in by_scope.items()}


def _record_findings(recorder, run_id, risks: list[AggregatedRisk]) -> None:
    """Persist each aggregated risk as an obs_finding row (best-effort; never raises)."""
    if recorder is None or not run_id:
        return
    thresholds = {"band_high_min": MATRIX_BAND_HIGH_MIN, "band_medium_min": MATRIX_BAND_MEDIUM_MIN}
    for risk in risks:
        recorder.record_finding({
            "run_id": run_id,
            "origin": OBS_ORIGIN_AGGREGATED,
            "scope_area": risk.scope_area,
            "risk_type": risk.risk_type,
            "risk_category": risk.risk_category,
            "likelihood": risk.likelihood,
            "impact": risk.impact,
            "matrix_score": risk.matrix_score,
            "llm_confidence": risk.llm_confidence,
            "derived_confidence": risk.derived_confidence,
            "thresholds_applied": thresholds,
            "is_recurrence": risk.past_occurrence,
            "past_occurrence_ref": risk.past_occurrence_link or None,
        })


def run_aggregation(
    aggregation_agent,
    retriever: KnowledgeRetriever | None,
    output_rows: list[OutputRow],
    transactional: list[RiskOutput],
    recorder=None,
    run_id: str | None = None,
) -> tuple[list[AggregatedRisk], dict]:
    """Cluster findings into unique risks, set Past Occurrence, and compute the scope rollup.

    When a recorder + run_id are supplied, each aggregated risk is also written to obs_finding
    (with its confidence + thresholds) for observability. Returns (aggregated_risks, scope_overall_map).
    """
    raws = build_raw_findings(output_rows, transactional)
    if not raws:
        logger.info("[Aggregation] No reportable findings to cluster — skipping aggregation")
        return [], {}

    logger.info(
        "[Aggregation] Clustering %d reportable findings into unique risks "
        "(single LLM call — may take a few minutes)…", len(raws),
    )
    risks = aggregation_agent.aggregate(raws)
    if retriever is not None:
        logger.info("[Aggregation] Applying recurrence lookups | risks=%d", len(risks))
        # Whether ANY historical KB exists for this auditor — distinguishes a searched "No" from
        # "Unable to determine based on available evidence" (no history to search), per reviewer feedback.
        try:
            kb_available = retriever.has_recurrence_history()
        except Exception:  # noqa: BLE001
            kb_available = True
        for position, risk in enumerate(risks, start=1):
            try:
                _apply_recurrence(risk, retriever)
                _apply_corroboration(risk, retriever)
            except Exception as exc:
                logger.warning("[Aggregation] recurrence/corroboration lookup skipped | error=%s", exc)
            risk.past_occurrence_determinable = kb_available
            if position % 10 == 0 or position == len(risks):
                logger.info("[Aggregation] Recurrence lookups | %d/%d", position, len(risks))

    _record_findings(recorder, run_id, risks)
    scope_overall = _scope_overall(risks)
    logger.info(
        "[Aggregation] %d raw findings -> %d unique risks across %d scope area(s)",
        len(raws), len(risks), len(scope_overall),
    )
    return risks, scope_overall
