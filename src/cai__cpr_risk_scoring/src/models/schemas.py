from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class RiskLevel(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    NO_RISK = "No Risk"


class DocumentSource(str, Enum):
    HCBI = "hcbi"
    POLICY = "policy"
    SUPPORTING = "supporting"
    SCOPE_POLICY = "scope_policy"
    TRANSACTIONAL_ANALYSIS = "transactional_analysis"
    PAST_AUDIT = "past_audit"
    SI_HISTORY = "si_history"
    LEGAL = "legal"
    ADDITIONAL_INPUT = "additional_input"
    LOCAL_POLICY = "local_policy"
    RAW_TRANSACTIONAL = "raw_transactional"
    QUESTIONNAIRE = "questionnaire"


class RetrievalMode(str, Enum):
    SEMANTIC = "semantic"
    TARGETED = "targeted"


class ConflictSeverity(str, Enum):
    CRITICAL = "Critical"
    MODERATE = "Moderate"
    MINOR = "Minor"


class ExtractedDocument(BaseModel):
    file_path: str
    file_name: str
    file_type: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    scope_id: str
    page_count: Optional[int] = None

    @field_validator("file_type")
    @classmethod
    def normalise_file_type(cls, v: str) -> str:
        return v.lower().lstrip(".")


class DocumentChunk(BaseModel):
    chunk_id: str
    document_name: str
    source: DocumentSource
    content: str
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    scope_id: str
    auditor_folder: Optional[str] = None
    embedding: Optional[list[float]] = None
    similarity_score: Optional[float] = None

    @field_validator("token_count")
    @classmethod
    def validate_token_count(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("token_count must be positive")
        return v

    @field_validator("similarity_score")
    @classmethod
    def validate_similarity(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError("similarity_score must be between 0.0 and 1.0")
        return v


class AuditQuestion(BaseModel):
    question_id: str
    question_text: str
    response_text: str
    followup_question: str = ""
    followup_response: str = ""
    scope_id: str
    auditor_id: str
    questionnaire_type: str
    source_table: str = ""  # questionnaire table/file this Q&A came from (for output traceability)
    original_columns: dict[str, Any] = Field(default_factory=dict)

    @field_validator("question_text", "response_text")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

    @field_validator("questionnaire_type")
    @classmethod
    def validate_questionnaire_type(cls, v: str) -> str:
        allowed = {"planning", "idr"}
        if v.lower() not in allowed:
            raise ValueError(f"questionnaire_type must be one of {allowed}")
        return v.lower()


class ComplianceGap(BaseModel):
    gap_id: str
    description: str
    policy_reference: str
    severity: RiskLevel
    hcbi_section: Optional[str] = None


class TransactionalResult(BaseModel):
    table_name: str
    sql_query: str
    result_rows: list[dict[str, Any]]
    row_count: int
    kpi_summary: str
    scope_filter_applied: bool = True

    @model_validator(mode="after")
    def validate_row_count(self) -> TransactionalResult:
        if self.row_count != len(self.result_rows):
            raise ValueError("row_count does not match actual number of result_rows")
        return self


class ConflictFlag(BaseModel):
    conflict_id: str
    question_id_a: str
    question_id_b: str
    similarity_score: float
    severity: ConflictSeverity
    description: str
    flagged_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("similarity_score")
    @classmethod
    def validate_score(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("similarity_score must be between 0.0 and 1.0")
        return v


class RiskFinding(BaseModel):
    question_id: str
    scope_id: str
    auditor_id: str

    hcbi_chunks: list[DocumentChunk] = Field(default_factory=list)
    policy_chunks: list[DocumentChunk] = Field(default_factory=list)

    semantic_chunks: list[DocumentChunk] = Field(default_factory=list)
    targeted_chunks: list[DocumentChunk] = Field(default_factory=list)

    compliance_gaps: list[ComplianceGap] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.NO_RISK
    transactional_intent_detected: bool = False
    transactional_results: list[TransactionalResult] = Field(default_factory=list)

    risk_score: float = Field(default=0.0, ge=0.0, le=10.0)
    scope_assignment: str = ""
    validation_score: float = Field(default=0.0, ge=0.0, le=10.0)
    ai_recommendation: str = ""
    calculation_evidence: str = ""

    conflicts: list[ConflictFlag] = Field(default_factory=list)


class OutputRow(BaseModel):
    """
    One row in the final audit output Excel.
    Only Potential Risk and Further Review Required findings are written.
    No Risk rows are never written to output.
    """
    sno:                     int
    source_file:             str = ""  # questionnaire file/table this row's Q&A came from

    risk_title:              str
    risk_type:               str

    @field_validator("risk_type", mode="before")
    @classmethod
    def _enforce_single_line(cls, v: str) -> str:
        return next((line.strip() for line in str(v).splitlines() if line.strip()), str(v))
    risk_category:           str
    risk_category_reasoning: str

    risk_rationale:          str
    risk_description:        str
    scope_area:              str = "Not Applicable"
    scope_area_reasoning:    str = ""

    calculation_evidence:    Optional[str] = None
    sql_queries:             Optional[str] = None
    samples:                 str = ""
    evidence_explanation:    str = ""

    question:                Optional[str] = None
    response:                Optional[str] = None
    followup_question:       Optional[str] = None
    followup_response:       Optional[str] = None

    source_details:          str
    processed_at:            datetime = Field(default_factory=datetime.utcnow)


class ScopeAreaSummary(BaseModel):
    """Output B — top-level summary per scope area."""
    scope_id: str
    scope_name: str
    total_questions: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    no_risk_count: int
    average_risk_score: float = Field(ge=0.0, le=10.0)
    top_compliance_gaps: list[str] = Field(default_factory=list)
    transactional_findings_summary: str = ""
    overall_risk_rating: RiskLevel
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def validate_question_counts(self) -> ScopeAreaSummary:
        total = (
            self.high_risk_count
            + self.medium_risk_count
            + self.low_risk_count
            + self.no_risk_count
        )
        if total != self.total_questions:
            raise ValueError(
                f"Risk level counts ({total}) do not sum to total_questions ({self.total_questions})"
            )
        return self


class AuditState(BaseModel):
    """Shared state passed between all LangGraph nodes."""
    question: AuditQuestion
    risk_finding: RiskFinding = Field(default=None)
    processing_complete: bool = False
    error: Optional[str] = None

    @model_validator(mode="after")
    def initialise_risk_finding(self) -> AuditState:
        if self.risk_finding is None:
            self.risk_finding = RiskFinding(
                question_id=self.question.question_id,
                scope_id=self.question.scope_id,
                auditor_id=self.question.auditor_id,
            )
        return self
