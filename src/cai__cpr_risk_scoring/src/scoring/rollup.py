"""Category rollup — combine multiple risk categories into one.

Used for the Risk Summary page's "Overall Risk Category", which the spec defines as the average of
all risk-type categories within a scope area. Categories are ranked (High=3 … No Risk=0), averaged,
and mapped back to a band. This aggregates ACROSS risks; it never rescales the per-finding matrix
score (that stays Likelihood x Impact, untouched).
"""

from src.config import constants


def average_category(categories: list[str]) -> str:
    """Return the average category across a list of category labels (Risk Summary rollup)."""
    ranks = [
        constants.CATEGORY_RANK[c]
        for c in categories
        if c in constants.CATEGORY_RANK
    ]
    if not ranks:
        return constants.RISK_CATEGORY_NONE

    avg = sum(ranks) / len(ranks)
    if avg >= constants.CATEGORY_AVG_HIGH_MIN:
        return constants.RISK_CATEGORY_HIGH
    if avg >= constants.CATEGORY_AVG_MEDIUM_MIN:
        return constants.RISK_CATEGORY_MEDIUM
    if avg >= constants.CATEGORY_AVG_LOW_MIN:
        return constants.RISK_CATEGORY_LOW
    return constants.RISK_CATEGORY_NONE
