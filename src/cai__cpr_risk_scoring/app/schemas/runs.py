"""Request/response models for starting and tracking runs."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.status_codes import ApiName


class RunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class QuestionnaireTables(BaseModel):
    """Questionnaire source tables for a run — explicit per type (one or more each). When supplied,
    each type must list at least one table."""

    idr: list[str] = Field(..., min_length=1, examples=[["idr_sam_1"]])
    planning: list[str] = Field(..., min_length=1, examples=[["consolidated_sam_1"]])


class StartRunRequest(BaseModel):
    """Common base for starting any of the three jobs — ONLY the fields every job shares.

    Risk-Analysis-specific ingestion inputs (questionnaire tables, supporting-docs / SI / Legal /
    Additional-Input / past-audit paths) live on RiskAnalysisStartRequest, so Scoping AI / Deck do
    NOT advertise them. Any field left unset falls back to the engine's configured default.
    """

    auditor_id: str = Field(..., examples=["Naeem_1"], description="Auditor folder id to run for.")
    engagement_numbers: str | None = Field(
        default=None,
        examples=["2000,2001"],
        description="Override of AUDIT_ENGAGEMENT_NUMBERS for this run (comma-separated).",
    )
    force_ingest: bool = Field(
        default=False, description="Re-ingest even if indexes are already populated (where supported)."
    )


class RiskAnalysisStartRequest(StartRunRequest):
    """Risk Analysis request — the full run inputs. Engagement numbers, questionnaire tables and the
    supporting-docs folder are MANDATORY; SI History, Legal, Additional Input, past-audit and the TPI
    inventory paths stay optional (not every auditor has them)."""

    engagement_numbers: str = Field(..., examples=["1000,1001"])
    questionnaire: QuestionnaireTables = Field(..., description="IDR + Planning tables (required).")
    supporting_docs_path: str = Field(..., description="Volume folder with supporting documents.")
    entity_name: str | None = Field(
        default=None, examples=["IM Australia"],
        description="Entity under audit — passed to the LLM as context for the analysis.",
    )
    scope_period_start: date | None = Field(
        default=None, examples=["2024-01-01"],
        description="Audit scope period start — passed to the LLM as context (the period under review).",
    )
    scope_period_end: date | None = Field(
        default=None, examples=["2024-12-31"],
        description="Audit scope period end — passed to the LLM as context (the period under review).",
    )
    si_history_path: str | None = Field(
        default=None, description="Volume path to this auditor's SI History inputs.",
    )
    legal_path: str | None = Field(
        default=None, description="Volume path to this auditor's Legal inputs.",
    )
    additional_input_path: str | None = Field(
        default=None, description="Volume path to this auditor's Additional Input knowledge base.",
    )
    past_audit_path: str | None = Field(
        default=None, description="Volume path to this auditor's past-audit reports.",
    )
    tpi_inventory_path: str | None = Field(
        default=None,
        description=(
            "Optional explicit TPI inventory file path. Normally the TPI inventory is auto-discovered "
            "in the supporting-docs folder by its TC name (SD_<eng>_IDR_<section>_Q#_<...TPI...>); "
            "provide this only as a fallback. If neither is found, TPI matching is skipped."
        ),
    )


class ScopingAIStartRequest(StartRunRequest):
    """Scoping AI request — reads Output C (Risk Analysis result). Default source is the Delta table
    (the `_copy` output table); pass source='excel' with a workbook path to use the Excel file."""

    source: str = Field(default="db_table", examples=["db_table", "excel"])
    path: str = Field(..., description="Output C Delta table FQN (db_table) or workbook path (excel).")
    output_e_path: str | None = Field(
        default=None,
        description="Output E (Past Audit Report) Delta table FQN — db_table source ONLY; include it to "
                    "add past-audit risks. Ignored for excel (read from the same workbook). Omit → skipped.",
    )
    output_f_path: str | None = Field(
        default=None,
        description="Output F (Scope Txn Summary) Delta table FQN — db_table source ONLY. Ignored for "
                    "excel. Omit → auto-derived from the Output C table's catalog.schema.",
    )


class ScopingDeckStartRequest(StartRunRequest):
    """Scoping Deck request — audit identity + scope period + the input workbooks/tables."""

    entity_name: str = Field(..., examples=["IM Australia"], description="Audit entity.")
    scope_period_start: date = Field(..., examples=["2024-01-01"])
    scope_period_end: date = Field(..., examples=["2024-12-31"])
    country: str | None = Field(default=None, examples=["Australia"])
    iso3: str | None = Field(default=None, examples=["AUS"])
    mrc_number: str | None = Field(default=None, examples=["MRC1234"])

    risk_scope_path: str = Field(..., description="AI Scope output (db_table FQN or workbook path).")
    past_review_path: str = Field(
        ..., description="Output E (Past Audit / Summary of Past Reviews) — db_table FQN or workbook path."
    )
    output_f_path: str | None = Field(
        default=None,
        description="Output F (Scope Txn Summary) Delta table FQN — db_table source ONLY. For excel it is "
                    "read from the same workbook as Output E (omit).",
    )
    source: str = Field(default="db_table", examples=["db_table", "excel"])


class Progress(BaseModel):
    current: int = 0
    total: int = 0


class RunAccepted(BaseModel):
    """202 response when a run is accepted."""

    run_id: str
    api: ApiName
    state: RunState
    status_url: str


class RunStatus(BaseModel):
    """Current snapshot of a run."""

    run_id: str
    api: ApiName
    auditor_id: str
    state: RunState
    status_code: str | None = None
    label: str | None = None
    progress: Progress | None = None
    result: dict | None = None
    error: str | None = None
    started_at: datetime
    updated_at: datetime
