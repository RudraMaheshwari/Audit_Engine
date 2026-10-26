from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator
from typing_extensions import TypedDict

from src.config.constants import (
    RISK_FURTHER_REVIEW, RISK_NO_RISK, RISK_POTENTIAL,
    SCOPE_CONFIDENCE_MIN,
)


class RiskType(str, Enum):
    POTENTIAL_RISK  = RISK_POTENTIAL
    FURTHER_REVIEW  = RISK_FURTHER_REVIEW
    NO_RISK         = RISK_NO_RISK


class DocumentType(str, Enum):
    GLOBAL_POLICY  = "GLOBAL_POLICY"
    TPI_POLICY     = "TPI_POLICY"
    SUPPORTING_DOC = "SUPPORTING_DOC"


class QuestionRow(BaseModel):
    unique_id:           str
    process_review_type: str
    document_name:       str
    sections:            Optional[str] = None
    question_no:         int
    question:            str
    company_response:    str
    supporting_document: Optional[str] = None
    followup_question:   Optional[str] = None
    followup_response:   Optional[str] = None
    synthetic_data:      Optional[str] = None

    @field_validator("question", "company_response", mode="before")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty or whitespace")
        return v.strip()


class ChunkMetadata(BaseModel):
    chunk_id:       str
    document_name:  str
    document_type:  DocumentType

    section_title:  Optional[str] = None
    section_no:     Optional[str] = None
    page_number:    Optional[int] = None

    version:        Optional[str] = None
    effective_date: Optional[str] = None
    language:       str           = "English"

    policy_domain:  Optional[str] = None
    policy_name:    Optional[str] = None
    policy_number:  Optional[str] = None
    country:        Optional[str] = None
    policy_owner:   Optional[str] = None

    index_name:          str                = ""
    file_format:         str                = ""
    ingestion_timestamp: Optional[datetime] = None
    parent_email:        Optional[str]      = None


class RetrievedChunk(BaseModel):
    chunk_id:        str
    content:         str
    relevance_score: float = Field(ge=0.0, le=1.0)
    metadata:        ChunkMetadata

    @field_validator("content", mode="before")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Chunk content must not be empty")
        return v.strip()


class ConflictFlag(BaseModel):
    has_conflict:              bool
    conflict_with_question_id: Optional[str]   = None
    similarity_score:          Optional[float] = Field(default=None, ge=0.0, le=1.0)
    description:               str             = "None"


class ScopeAssignment(BaseModel):
    scope_id:         str
    scope_label:      str
    confidence_score: float = Field(ge=0.0, le=1.0)
    is_primary_scope: bool
    note:             Optional[str] = None

    @field_validator("scope_id", mode="before")
    @classmethod
    def scope_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("scope_id must not be empty")
        return v.strip().upper()

    @field_validator("confidence_score", mode="before")
    @classmethod
    def flag_low_confidence(cls, v: float) -> float:
        return v


class SourceDetail(BaseModel):
    source_name:   str
    document_name: str
    section:       Optional[str] = None
    page_number:   Optional[int] = None
    question_id:   Optional[str] = None


class RiskOutput(BaseModel):
    risk_title:              RiskType
    risk_type:               str
    # Scored on the SAME Likelihood x Impact matrix as the classifier/aggregation. Axes are 0 and
    # category is "No Risk" for a No-Risk finding (No Risk is not matrix-scored).
    likelihood:              int = 0
    impact:                  int = 0
    matrix_score:            int = 0
    risk_category:           str = ""
    risk_score_reasoning:    str
    scope_area:              str

    risk_description:        str
    risk_rationale:          str
    # Local-language renderings for BILINGUAL Output A (set only when audit_local_language is configured
    # and translation succeeds). Output A stacks English + these; Output C / exports stay English-only.
    risk_description_local:  Optional[str] = None
    risk_rationale_local:    Optional[str] = None

    evidence_explanation:    str
    calculation_evidence:    Optional[str] = None
    sql_queries:             Optional[str] = None
    samples:                 str = ""

    question:                Optional[str] = None
    response:                Optional[str] = None
    followup_question:       Optional[str] = None
    followup_response:       Optional[str] = None

    source_details:          List[SourceDetail]

    @field_validator("risk_type", "risk_description", "risk_rationale", "risk_score_reasoning", "evidence_explanation", mode="before")
    @classmethod
    def no_empty_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Output field must not be empty")
        return v.strip()


class TransactionalQueryResult(BaseModel):
    was_triggered:   bool
    sql_query:       Optional[str] = None
    query_result:    Optional[str] = None
    validation_note: Optional[str] = None
    table_queried:   Optional[str] = None

    @field_validator("sql_query", "query_result", mode="before")
    @classmethod
    def strip_if_present(cls, v):
        return v.strip() if v and isinstance(v, str) else v


class AuditState(TypedDict):
    question_row:              QuestionRow
    retrieval_query:           str
    hcbi_chunks:               List[RetrievedChunk]
    due_diligence_chunks:      List[RetrievedChunk]
    supporting_doc_chunks:     List[RetrievedChunk]
    transactional_data_result: TransactionalQueryResult
    conflict_flag:             ConflictFlag
    aggregated_context:        str
    scope_assignment:          ScopeAssignment
    risk_output:               RiskOutput
