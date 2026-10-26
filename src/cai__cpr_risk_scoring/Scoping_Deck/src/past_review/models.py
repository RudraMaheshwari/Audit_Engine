"""Value objects for prior-review observations (past CPR and GACS findings)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReviewKind(Enum):
    """The kind of prior review an observation came from."""

    CPR = "cpr"
    GACS = "gacs"
    OTHER = "other"


@dataclass(frozen=True)
class PastReviewObservation:
    """One finding from a prior review, as read from Output E ('Summary of Past Reviews')."""

    scope_area: str
    issue_description: str
    criticality: str
    issue_type: str
    source_document: str
    audit_report: str
    report_year: int | None
    review_kind: ReviewKind = ReviewKind.OTHER
