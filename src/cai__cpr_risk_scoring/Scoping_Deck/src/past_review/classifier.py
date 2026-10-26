"""Classifies a prior-review observation as CPR or GACS by the terms in its source report.

The business uploads the past report against the questionnaire; the report text / document name carries
the identifying terms. GACS is checked first so a report naming both is treated as GACS.
"""

from __future__ import annotations

import re

from .models import ReviewKind

_GACS_PATTERN = re.compile(r"\bgacs\b", re.IGNORECASE)
_CPR_PATTERN = re.compile(
    r"\bcpr\b|compliance process review|management action plan|\bmap\b", re.IGNORECASE
)


class PastReviewClassifier:
    """Determines the ReviewKind of an observation from its report identifiers."""

    @staticmethod
    def classify(*sources: str) -> ReviewKind:
        text = " ".join(s for s in sources if s)
        if _GACS_PATTERN.search(text):
            return ReviewKind.GACS
        if _CPR_PATTERN.search(text):
            return ReviewKind.CPR
        return ReviewKind.OTHER
