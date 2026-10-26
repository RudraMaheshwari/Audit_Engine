"""Approved Risk Matrix: Score = Likelihood x Impact, mapped to a severity band.

The raw multiplication is never rescaled or averaged. Edge rules from the approved risk
scoring guidance are applied to the axes before multiplication:
  - a confirmed/high-probability foreign-official nexus sets an Impact floor,
  - missing expected evidence sets a Likelihood floor,
  - recurrence confirmed against current-year data raises Likelihood by one level.
"""

from dataclasses import dataclass

from src.config import constants


@dataclass(frozen=True)
class MatrixScore:
    """Result of scoring one finding on the approved matrix."""

    impact: int
    likelihood: int
    score: int
    category: str
    impact_label: str
    likelihood_label: str


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _band(score: int) -> str:
    if score >= constants.MATRIX_BAND_HIGH_MIN:
        return constants.RISK_CATEGORY_HIGH
    if score >= constants.MATRIX_BAND_MEDIUM_MIN:
        return constants.RISK_CATEGORY_MEDIUM
    return constants.RISK_CATEGORY_LOW


def band_confidence(score: int) -> float:
    """Derived confidence in the band assignment, from the score's distance to a band boundary.

    A score sitting deep inside its band (far from the High/Medium cutoffs) is a confident
    classification; a score right on a boundary is not. Distance to the nearest cutoff is normalised
    to 0.0–1.0 by the widest possible such distance.
    """
    distance = min(
        abs(score - constants.MATRIX_BAND_MEDIUM_MIN),
        abs(score - constants.MATRIX_BAND_HIGH_MIN),
    )
    scale = constants.MATRIX_SCORE_MAX - constants.MATRIX_BAND_HIGH_MIN
    if scale <= 0:
        return 0.0
    return round(min(1.0, distance / scale), 3)


def score_risk(
    impact: int,
    likelihood: int,
    *,
    foreign_official_nexus: bool = False,
    missing_expected_evidence: bool = False,
    recurrence_confirmed: bool = False,
) -> MatrixScore:
    """Apply the approved edge rules to the axes, then multiply and band the result."""
    effective_impact = _clamp(impact, constants.MATRIX_AXIS_MIN, constants.MATRIX_AXIS_MAX)
    effective_likelihood = _clamp(likelihood, constants.MATRIX_AXIS_MIN, constants.MATRIX_AXIS_MAX)

    if foreign_official_nexus:
        effective_impact = max(effective_impact, constants.FOREIGN_OFFICIAL_IMPACT_FLOOR)
    if missing_expected_evidence:
        effective_likelihood = max(
            effective_likelihood, constants.MISSING_EXPECTED_EVIDENCE_LIKELIHOOD_FLOOR
        )
    if recurrence_confirmed:
        effective_likelihood = _clamp(
            effective_likelihood + constants.RECURRENCE_LIKELIHOOD_INCREMENT,
            constants.MATRIX_AXIS_MIN,
            constants.MATRIX_AXIS_MAX,
        )

    score = effective_impact * effective_likelihood
    return MatrixScore(
        impact=effective_impact,
        likelihood=effective_likelihood,
        score=score,
        category=_band(score),
        impact_label=constants.MATRIX_IMPACT_LABELS[effective_impact],
        likelihood_label=constants.MATRIX_LIKELIHOOD_LABELS[effective_likelihood],
    )
