"""Risk scoring subsystem — the approved Impact x Likelihood matrix and its edge rules."""

from src.scoring.classification import (
    is_further_review,
    parse_matrix_axes,
    reconcile_category,
)
from src.scoring.risk_matrix import MatrixScore, band_confidence, score_risk
from src.scoring.rollup import average_category

__all__ = [
    "MatrixScore",
    "score_risk",
    "band_confidence",
    "parse_matrix_axes",
    "reconcile_category",
    "is_further_review",
    "average_category",
]
