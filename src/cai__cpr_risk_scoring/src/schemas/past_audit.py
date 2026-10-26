"""Data model for the Past Audit Report sheet.

Each past-audit report may contain multiple observations; each observation becomes one row. The AI
extracts the fields below from the report text held in the past-audit index (scoped to the auditor).
"""

from pydantic import BaseModel

_NOT_AVAILABLE = "Not Available"


class PastAuditObservation(BaseModel):
    """One observation extracted from a past audit report."""

    source_document: str
    report_date: str = _NOT_AVAILABLE
    scope_area: str = _NOT_AVAILABLE
    issue_type: str = ""
    issue_description: str = ""
    criticality: str = _NOT_AVAILABLE
    audit_report: str = ""
