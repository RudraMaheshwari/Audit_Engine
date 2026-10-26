"""Build the AI Scope rows: one per scope area, with the 4 fields.

Fields:
  1. Scope Area           — as-is from Output C.
  2. Overall Risk Category — avg(Likelihood x Impact) across the scope's rows -> band.
  3. Proposed Sample Size — from the CPR guidance workbook, per scope, no buffer.
  4. Summary of Risks     — an audit-friendly STORY: an exact totals line from Output F (transactions +
     amount) when the scope is transactional, then a short narrative from the scope's Output C
     Description(s) + Rationale(s) with its transactional-spend share woven in (no exact numbers /
     citations in the narrative). Source-recommended risks are narrated as a short story too.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import constants
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.observability.simple import trace_run
from src.utils.logger import get_logger

from .config import AIScopeConfig
from .guidance_loader import GuidanceSampleSizes
from .output_c_loader import load_output_c
from .output_f_loader import load_scope_txn_totals
from .sample_size_suggester import suggest_sample_size
from .scope_aggregator import group_by_scope
from .scope_key import normalize
from .source_risk_appender import append_source_recommended_risks
from .summary_generator import generate_summary

logger = get_logger(__name__)


@dataclass
class AIScopeRow:
    scope_area: str
    overall_risk_category: str
    proposed_sample_size: int | None
    summary_of_risks: str
    average_score: float | None = None
    scored_rows: int = 0
    scope_decision: str = "Included"
    # Origin of the row: "System" for our analysis; one of "SI History", "Legal", "Additional Input",
    # "Past Audit", "Management" for Direction-2 rows appended for risk types those sources raised.
    source: str = "System"
    # Source Evidence (Direction-2 rows only): Management → the exact questionnaire Q & Response; Past
    # Audit → the summarised Output E row + source details; SI/Legal/Additional & System → blank.
    source_evidence: str = ""


def build_ai_scope(config: AIScopeConfig) -> list[AIScopeRow]:
    """Read Output C, group by scope, and produce one AIScopeRow per scope area."""
    rows = load_output_c(config)
    if not rows:
        logger.warning("[AIScope] No Output C rows loaded — nothing to build")
        return []

    groups = group_by_scope(rows)
    guidance = GuidanceSampleSizes.load(config.guidance_path, config.settings)
    # Per-scope transaction totals (Output F) for the story's first line + spend-share percentage.
    txn_totals, grand_total_usd = load_scope_txn_totals(config)
    _trace = trace_run(config.settings, engine="ai_scope", auditor_id=config.settings.auditor_id)
    llm = AzureOpenAILLMProvider(config.settings).get_client()

    results: list[AIScopeRow] = []
    for group in groups:
        sample_size = guidance.lookup(group.scope_area)
        if sample_size is None:
            # Guidance has no value for this scope — fall back to an AI-suggested, risk-based size.
            sample_size = suggest_sample_size(llm, group)
            if sample_size is None:
                logger.warning(
                    "[AIScope] No guidance sample size and AI suggestion unavailable for scope %r — left blank",
                    group.scope_area,
                )
        txn = txn_totals.get(normalize(group.scope_area))
        summary = generate_summary(llm, group, txn, grand_total_usd)
        display_scope = constants.SCOPE_ID_TO_DISPLAY_NAME.get(group.scope_area, group.scope_area)
        results.append(AIScopeRow(
            scope_area=display_scope,
            overall_risk_category=group.overall_risk_category,
            proposed_sample_size=sample_size,
            summary_of_risks=summary,
            average_score=group.average_score,
            scored_rows=group.scored_rows,
        ))
        logger.info(
            "[AIScope] Built scope=%r | category=%s | sample=%s | scored_rows=%d",
            group.scope_area, group.overall_risk_category or "(blank)",
            sample_size if sample_size is not None else "(blank)", group.scored_rows,
        )

    logger.info("[AIScope] Built %d system AI Scope row(s)", len(results))

    # Direction-2: append risk types the KB sources (SI/Legal, Past audit, Management) raised but our
    # system did not cover. Best-effort — never aborts AI Scope if the indexes/LLM are unavailable.
    try:
        appended = append_source_recommended_risks(config, results, llm, guidance)
        if appended:
            logger.info("[AIScope] Appended %d source-recommended risk row(s)", len(appended))
            results.extend(appended)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AIScope] Source-recommended append skipped | error=%s", exc)

    _trace.finish()
    return results
