"""Data model for the risk-centric aggregation layer.

The per-question pipeline and Phase-3 transactional analysis both produce findings. Aggregation
normalises them into `RawFinding`s, the clustering agent groups those into unique `AggregatedRisk`s
(one line per risk type, one scope area, evidence clubbed), and each aggregated risk keeps its
`sources` so the UI's View Details can show the exact questions/transactions behind it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.schemas import OutputRow
from src.schemas.state import RiskOutput

SOURCE_TYPE_QUESTIONNAIRE = "questionnaire"
SOURCE_TYPE_TRANSACTIONAL = "transactional"


class RawFinding(BaseModel):
    """A single pre-aggregation finding from a question or from transactional analysis."""

    source_ref: str
    source_type: str
    source_table: str = ""
    scope_area: str = ""
    risk_type: str = ""
    risk_category: str = ""
    # Matrix axes carried from a transactional finding so a single-source transactional cluster can
    # keep its own (data-informed) band instead of being re-scored from the description text.
    likelihood: int = 0
    impact: int = 0
    matrix_score: int = 0
    description: str = ""
    question: str = ""
    response: str = ""
    followup_question: str = ""
    followup_response: str = ""
    source_details: str = ""
    calculation_evidence: str = ""
    sql_queries: str = ""


class AggregatedRiskSource(BaseModel):
    """One evidence pointer behind an aggregated risk (for View Details)."""

    source_ref: str
    source_type: str
    source_table: str = ""
    question: str = ""
    response: str = ""
    followup_question: str = ""
    followup_response: str = ""


class AggregatedRisk(BaseModel):
    """One clustered risk — a unique risk type under a single scope area.

    The three buckled source groups consolidate, across every finding in the cluster:
      - questionnaire_sources: the questionnaire files/tables the findings came from,
      - source_details: the relevant retrieved chunks / citations,
      - questions_and_responses: the questions, follow-ups, and responses.
    """

    scope_area: str
    risk_type: str
    risk_description: str
    risk_rationale: str
    risk_category: str
    overall_risk_category: str = ""
    likelihood: int = 0
    impact: int = 0
    matrix_score: int = 0
    llm_confidence: float = 0.0
    derived_confidence: float = 0.0
    calculation_evidence: str = ""
    sql_queries: str = ""
    past_occurrence: bool = False
    past_occurrence_link: str = ""
    # False ONLY when no historical KB (past audit / SI History / Legal / Additional Input) exists for
    # this auditor to search — recurrence then cannot be determined, so the output shows "Unable to
    # determine based on available evidence" instead of a bare "No" (reviewer: data limited to the
    # assessment period does not confirm a non-recurrence).
    past_occurrence_determinable: bool = True

    questionnaire_sources: str = ""
    source_details: str = ""
    questions_and_responses: str = ""

    sources: list[AggregatedRiskSource] = Field(default_factory=list)


def raw_finding_from_output_row(row: OutputRow) -> RawFinding:
    """Adapt a per-question OutputRow into a RawFinding for clustering."""
    return RawFinding(
        source_ref=str(row.sno),
        source_type=SOURCE_TYPE_QUESTIONNAIRE,
        source_table=row.source_file or "",
        scope_area=row.scope_area or "",
        risk_type=row.risk_type or "",
        risk_category=row.risk_category or "",
        description=row.risk_description or "",
        question=row.question or "",
        response=row.response or "",
        followup_question=row.followup_question or "",
        followup_response=row.followup_response or "",
        source_details=row.source_details or "",
    )


def raw_finding_from_risk_output(finding: RiskOutput) -> RawFinding:
    """Adapt a Phase-3 transactional RiskOutput into a RawFinding for clustering."""
    table = finding.source_details[0].document_name if finding.source_details else ""
    # Transactional sources cite the dashboard/table directly: "Source: <table_name>" (consistent
    # with the transactional analysis sheet; no "Transactional Data Analysis"/File Name/Section labels).
    rendered_details = "\n".join(
        f"• Source: {d.document_name}" for d in finding.source_details
    )
    return RawFinding(
        source_ref=f"TXN::{finding.scope_area}",
        source_type=SOURCE_TYPE_TRANSACTIONAL,
        source_table=table,
        scope_area=finding.scope_area or "",
        risk_type=finding.risk_type or "",
        risk_category=finding.risk_category or "",
        likelihood=finding.likelihood,
        impact=finding.impact,
        matrix_score=finding.matrix_score,
        description=finding.risk_description or "",
        source_details=rendered_details,
        calculation_evidence=finding.calculation_evidence or "",
        sql_queries=finding.sql_queries or "",
    )
