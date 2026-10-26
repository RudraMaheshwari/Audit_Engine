"""Group Output C rows by scope area and compute the Overall Risk Category.

Overall Risk Category is the HIGHEST per-risk "Risk Category" present in the scope — taken straight from
Output C's Risk Category column (the authoritative per-risk band), with the matrix band of
Likelihood x Impact used only as a fallback when that cell is blank. The scope takes the highest of
those bands; it deliberately does NOT average, so a single High risk keeps the whole scope High
(averaging would dilute a high row down into Medium/Low). Rows with no category are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.config import constants
from src.utils.logger import get_logger

from .output_c_loader import RiskRow
from .scope_key import normalize

logger = get_logger(__name__)


@dataclass
class ScopeGroup:
    scope_area: str                 # display name (first seen for the scope)
    rows: list[RiskRow] = field(default_factory=list)
    overall_risk_category: str = ""
    average_score: float | None = None
    scored_rows: int = 0            # how many rows had both L and I


def _band(score: float) -> str:
    if score >= constants.MATRIX_BAND_HIGH_MIN:
        return constants.RISK_CATEGORY_HIGH
    if score >= constants.MATRIX_BAND_MEDIUM_MIN:
        return constants.RISK_CATEGORY_MEDIUM
    return constants.RISK_CATEGORY_LOW


# Severity rank of a Risk Category label, for picking the HIGHEST in a scope (never average).
# High=3, Medium=2, Low=1, everything else (No Risk / Not Applicable / blank) = 0.
def _cat_rank(category: str) -> int:
    c = (category or "").strip().lower()
    if "high" in c:
        return 3
    if "medium" in c:
        return 2
    if "low" in c:
        return 1
    return 0


_RANK_TO_CAT = {
    3: constants.RISK_CATEGORY_HIGH,
    2: constants.RISK_CATEGORY_MEDIUM,
    1: constants.RISK_CATEGORY_LOW,
}


def group_by_scope(rows: list[RiskRow]) -> list[ScopeGroup]:
    """Collapse risk rows into one ScopeGroup per scope area, with the averaged-L×I category."""
    groups: dict[str, ScopeGroup] = {}
    for row in rows:
        key = normalize(row.scope_area)
        group = groups.get(key)
        if group is None:
            group = ScopeGroup(scope_area=row.scope_area)
            groups[key] = group
        else:
            logger.debug("[Aggregate] Merging row into existing group | scope=%r | key=%r", row.scope_area, key)
        group.rows.append(row)

    for group in groups.values():
        best_rank = 0
        scored = 0
        for r in group.rows:
            # Prefer Output C's per-risk Risk Category; fall back to the matrix band of L x I only when
            # that cell is blank. Any High row keeps the scope High (never diluted by averaging).
            cat = (getattr(r, "risk_category", "") or "").strip()
            if not cat and r.likelihood is not None and r.impact is not None:
                cat = _band(r.likelihood * r.impact)
            rank = _cat_rank(cat)
            if rank > 0:
                scored += 1
                best_rank = max(best_rank, rank)
        group.scored_rows = scored
        group.average_score = None  # no averaging — Overall is the highest Risk Category present
        if best_rank > 0:
            group.overall_risk_category = _RANK_TO_CAT[best_rank]
        else:
            group.overall_risk_category = ""  # no categorised rows (no Risk Category, no L/I)
            logger.warning(
                "[Aggregate] Scope %r has no categorised rows (no Risk Category, no L/I) — "
                "Overall Risk Category left blank", group.scope_area,
            )

    ordered = sorted(groups.values(), key=lambda g: g.scope_area.lower())
    logger.info(
        "[Aggregate] %d risk row(s) -> %d scope area(s)", len(rows), len(ordered)
    )
    return ordered
