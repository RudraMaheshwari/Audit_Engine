"""The status-code taxonomy for the three APIs — the single source of truth for codes + display labels.

A job's ProgressReporter emits one of these codes; the API exposes the code plus its human-readable
label (and, for loop phases, a {current, total} progress). Ordered tuples double as the canonical
phase sequence for each API.
"""

from __future__ import annotations

from enum import Enum


class ApiName(str, Enum):
    RISK_ANALYSIS = "risk_analysis"
    SCOPING_AI = "scoping_ai"
    SCOPING_DECK = "scoping_deck"


# (code, display label) in phase order. Sub-steps (RA_Q_*) are per-question and nested under
# RA_PROCESS_QUESTIONS; they are listed here so the label lookup covers them.
RISK_ANALYSIS_PHASES: tuple[tuple[str, str], ...] = (
    ("RA_INGEST_SUPPORTING",     "Reviewing supporting documents"),
    ("RA_INGEST_PAST_AUDITS",    "Reviewing past audit reports"),
    ("RA_SUMMARIZE_PAST_AUDITS", "Generating summary of past audits"),
    ("RA_INGEST_SI_LEGAL",       "Reviewing SI History and Legal Inputs"),
    ("RA_INGEST_POLICIES",       "Loading policies & scope guidance"),
    ("RA_ANALYZE_TRANSACTIONS",  "Analyzing transactional data"),
    ("RA_PROCESS_QUESTIONS",     "Analyzing questionnaire responses"),
    ("RA_Q_REVIEW_IDR",          "Reviewing Initial Document Request"),
    ("RA_Q_REVIEW_PLANNING",     "Reviewing Planning Questionnaires"),
    ("RA_Q_IDENTIFY_RISKS",      "Identifying potential risks"),
    ("RA_Q_MAP_SCOPE",           "Mapping risks to scope areas"),
    ("RA_Q_ASSIGN_CATEGORY",     "Assigning risk category"),
    ("RA_GENERATE_SUMMARY",      "Generating AI Risk Summary"),
)

SCOPING_AI_PHASES: tuple[tuple[str, str], ...] = (
    ("AS_ORGANIZE_SCOPES",       "Organizing the scope details"),
    ("AS_FETCH_SAMPLE_CATEGORY", "Fetching proposed sample size and risk category"),
    ("AS_SUMMARIZE_SCOPE_RISKS", "Summarizing risks for each scope area"),
)

SCOPING_DECK_PHASES: tuple[tuple[str, str], ...] = (
    ("SD_MARKET_OVERVIEW",          "Generating content for Market Overview"),
    ("SD_FETCH_CPI",                "Fetching details for CPI"),
    ("SD_UPDATE_CPI",               "Updating CPI details on the slide"),
    ("SD_BUSINESS_OVERVIEW",        "Generating content for Business Overview"),
    ("SD_SUMMARIZE_MGMT_RISKS",     "Summarizing risks noted by management"),
    ("SD_SUMMARIZE_LEGAL",          "Summarizing Legal Inputs"),
    ("SD_SUMMARIZE_SI_HISTORY",     "Summarizing SI History"),
    ("SD_SUMMARIZE_PRIOR_CPR",      "Summarizing Prior CPR findings"),
    ("SD_SUMMARIZE_PRIOR_GACS",     "Summarizing Prior GACS review findings"),
    ("SD_UPDATE_SCOPE_DETAILS",     "Updating proposed scope details"),
    ("SD_UPDATE_AREAS_NOT_IN_SCOPE", "Updating areas not in scope"),
    ("SD_ASSEMBLE_DECK",            "Finalizing deck"),
)

PHASES_BY_API: dict[ApiName, tuple[tuple[str, str], ...]] = {
    ApiName.RISK_ANALYSIS: RISK_ANALYSIS_PHASES,
    ApiName.SCOPING_AI: SCOPING_AI_PHASES,
    ApiName.SCOPING_DECK: SCOPING_DECK_PHASES,
}

# Flat code -> label lookup across all APIs.
STATUS_LABELS: dict[str, str] = {
    code: label for phases in PHASES_BY_API.values() for code, label in phases
}


def label_for(code: str) -> str:
    """Human-readable label for a status code (falls back to the code itself)."""
    return STATUS_LABELS.get(code, code)
