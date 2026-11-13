"""Reconcile the classifier's text block to the deterministic Risk Matrix band.

The classifier assigns the Likelihood and Impact integers (applying the approved edge rules in its
judgement). This module owns the arithmetic: it parses those axes and forces the Risk Category line
to the band that Score = Likelihood x Impact produces, so the category can never be mis-banded. It
leaves the category untouched for No Risk findings, where no matrix axes apply.
"""

import re

from src.config import constants
from src.scoring.risk_matrix import MatrixScore, score_risk

_LIKELIHOOD_RE = re.compile(r"^Likelihood:\s*([1-5])", re.MULTILINE)
_IMPACT_RE = re.compile(r"^Impact:\s*([1-5])", re.MULTILINE)
_TYPE_RE = re.compile(r"^Risk Type:\s*(.+)$", re.MULTILINE)
_CATEGORY_LINE_RE = re.compile(r"^Risk Category:.*$", re.MULTILINE)
_TYPE_LINE_RE = re.compile(r"^Risk Type:.*$", re.MULTILINE)
_LIKELIHOOD_LINE_RE = re.compile(r"^Likelihood:.*$", re.MULTILINE)
_IMPACT_LINE_RE = re.compile(r"^Impact:.*$", re.MULTILINE)


def downgrade_to_further_review(classification_text: str, reason: str) -> str:
    """Deterministically rewrite a scored classifier block to Further Review Required (no band).

    Used as a HARD, code-level gate: a scored transactional/quantitative risk is only valid when it is
    backed by captured, audit-scoped calculation evidence. When that evidence is missing or unscoped the
    matrix must NOT apply, so this flips Risk Type to Further Review Required, the Risk Category to
    "Not Applicable", and the Likelihood/Impact axes to N/A — and appends the reason so the downgrade is
    visible to the writer and reviewer. (The equivalent rule in the classifier prompt is advisory; this
    enforces it regardless of what the LLM emitted.)
    """
    text = _TYPE_LINE_RE.sub(f"Risk Type: {constants.RISK_FURTHER_REVIEW}", classification_text, count=1)
    text = _CATEGORY_LINE_RE.sub("Risk Category: Not Applicable", text, count=1)
    text = _LIKELIHOOD_LINE_RE.sub("Likelihood: N/A", text, count=1)
    text = _IMPACT_LINE_RE.sub("Impact: N/A", text, count=1)
    return f"{text}\nCalc Evidence Gate: downgraded to Further Review Required — {reason}."


def parse_matrix_axes(text: str) -> tuple[int | None, int | None]:
    """Return (likelihood, impact) integers parsed from the classifier block, or None if absent."""
    likelihood = _LIKELIHOOD_RE.search(text)
    impact = _IMPACT_RE.search(text)
    return (
        int(likelihood.group(1)) if likelihood else None,
        int(impact.group(1)) if impact else None,
    )


def _is_no_risk(text: str) -> bool:
    match = _TYPE_RE.search(text)
    return bool(match) and constants.RISK_NO_RISK.lower() in match.group(1).strip().lower()


def _is_further_review(text: str) -> bool:
    match = _TYPE_RE.search(text)
    return bool(match) and constants.RISK_FURTHER_REVIEW.lower() in match.group(1).strip().lower()


def is_further_review(text: str) -> bool:
    """Public: True when the classification block's Risk Type is Further Review Required.

    Used to label the (unscored) Risk Category as 'Not Applicable' rather than 'No Risk' for
    observability, keeping it consistent with the per-question sheet.
    """
    return _is_further_review(text)


def reconcile_category(classification_text: str) -> tuple[str, MatrixScore | None]:
    """Force the Risk Category line to the deterministic matrix band.

    Returns the (possibly rewritten) classification text and the MatrixScore. For No Risk and Further
    Review Required findings — which are never scored/banded — or when the axes are missing, the text is
    returned unchanged and the score is None. The Risk Matrix applies ONLY to Potential Risk.
    """
    if _is_no_risk(classification_text) or _is_further_review(classification_text):
        return classification_text, None

    likelihood, impact = parse_matrix_axes(classification_text)
    if likelihood is None or impact is None:
        return classification_text, None

    result = score_risk(impact, likelihood)
    updated = _CATEGORY_LINE_RE.sub(
        f"Risk Category: {result.category}", classification_text, count=1
    )
    return updated, result
