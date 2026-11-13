"""Classifies a prior-review observation as CPR or GACS by the terms in its source report.

The business uploads the past report against the questionnaire; the report FILE NAME carries the
identifying terms. Matching is case-insensitive SUBSTRING (not strict word-boundary) because real
names attach the terms to other characters ('GACS_2024', 'CPR-Report.pdf', '..._GRID.xlsx').

Decision order (first hit wins) — tuned to this program's actual report names, e.g.
'Janssen Korea FCPA Process Review_GRID.xlsx' vs 'Janssen Korea FCPA Review_Executive Summary.pptx':
  1. explicit 'gacs'                         -> GACS   (an explicit name always wins)
  2. 'process review' / 'cpr' / 'compliance process review' / 'management action plan' / \\bmap\\b
                                             -> CPR    (a Compliance PROCESS Review)
  3. 'fcpa' (an FCPA review that is NOT a process review, so it fell through step 2)
                                             -> GACS   (the FCPA anti-corruption review family)
  4. otherwise                               -> OTHER

Step 2 MUST precede step 3: 'FCPA Process Review' contains 'fcpa', so testing FCPA first would
wrongly bucket every process review as GACS. 'map' stays word-bounded so it never matches inside
'mapping' / 'roadmap'.

If your program labels these the other way round (plain 'FCPA Review' = CPR, 'Process Review' = GACS),
swap the ReviewKind returned by steps 2 and 3 — nothing else needs to change.
"""

from __future__ import annotations

import re

from .models import ReviewKind

_GACS_EXPLICIT = re.compile(r"gacs", re.IGNORECASE)
_CPR_PATTERN = re.compile(
    r"process review|compliance process review|cpr|management action plan|\bmap\b", re.IGNORECASE
)
# An FCPA review that is NOT a process review (step 2 already consumed those) -> GACS family.
_GACS_IMPLIED = re.compile(r"fcpa", re.IGNORECASE)


class PastReviewClassifier:
    """Determines the ReviewKind of an observation from its report identifiers."""

    @staticmethod
    def classify(*sources: str) -> ReviewKind:
        text = " ".join(s for s in sources if s)
        if _GACS_EXPLICIT.search(text):
            return ReviewKind.GACS
        if _CPR_PATTERN.search(text):
            return ReviewKind.CPR
        if _GACS_IMPLIED.search(text):
            return ReviewKind.GACS
        return ReviewKind.OTHER
