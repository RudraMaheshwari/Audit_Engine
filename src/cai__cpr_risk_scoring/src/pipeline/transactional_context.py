"""Format Phase-3 transactional findings into a compact current-year context block.

Phase 3 runs first and produces per-scope transactional risk findings. This module renders them as a
bounded summary injected into every question's Phase-2 analysis so the model knows WHICH scopes/controls
the current-year data flagged. It is DIRECTIONAL CONTEXT ONLY: specific figures are withheld here on
purpose so the model cannot cite a Phase-3 amount it did not compute — any quantitative claim that
matters must be re-derived by the per-question transactional (NL-to-SQL) agent (whose output lands under
"=== TRANSACTIONAL DATA ===") and only that calculated figure may be cited.
"""

import re

from src.schemas.state import RiskOutput

_MAX_FINDINGS = 40
_MAX_DESCRIPTION_CHARS = 300

_HEADER = (
    "=== TRANSACTIONAL ANALYSIS (CURRENT-YEAR DATA) — DIRECTIONAL CONTEXT ONLY ===\n"
    "Areas the current-year transactional analysis flagged for examination — use these ONLY to decide "
    "WHICH scopes/controls to look into. Specific figures are withheld here ON PURPOSE: do NOT quote any "
    "amount, count, or percentage from this block, and do not treat it as evidence by itself. If a "
    "quantitative claim matters for this question, it must be re-derived by the transactional (NL-to-SQL) "
    "agent for THIS question — it will appear under '=== TRANSACTIONAL DATA ===', and ONLY that calculated "
    "figure may be cited. A prior-year finding becomes a current-year risk only when re-confirmed there."
)

# Currency amounts, comma-grouped numbers, and percentages are masked so no figure can be copied from
# the context. Bare small integers and 4-digit years are left intact (they carry no borrowable amount).
_FIGURE_RE = re.compile(
    r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?"                                    # $1,156,648.00
    r"|\b(?:USD|EUR|GBP|AUD|NZD|CAD|JPY|KRW|CNY|INR)\s?\d[\d,]*(?:\.\d+)?"  # USD 1,156,648
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"                              # 1,156,648 / 396,684,381.18
    r"|\b\d+(?:\.\d+)?\s?%",                                          # 12.5%
    re.IGNORECASE,
)
_MASK = "[figure withheld — recompute via transactional agent]"


def _mask_figures(text: str) -> str:
    return _FIGURE_RE.sub(_MASK, text or "")


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_transactional_context(findings: list[RiskOutput]) -> str:
    """Render Phase-3 findings as a compact, bounded, FIGURE-FREE current-year direction block.

    Returns an empty string when there are no findings so callers can omit the section entirely. The
    exact calculation evidence is intentionally NOT included here — it belongs on the finding's own row
    and must be re-derived per question (see module docstring).
    """
    if not findings:
        return ""

    lines = [_HEADER]
    for position, finding in enumerate(findings[:_MAX_FINDINGS], start=1):
        lines.append(
            f"[{position}] Scope: {finding.scope_area} | {finding.risk_type}"
        )
        lines.append(f"    {_mask_figures(_truncate(finding.risk_description, _MAX_DESCRIPTION_CHARS))}")

    remaining = len(findings) - _MAX_FINDINGS
    if remaining > 0:
        lines.append(f"[+{remaining} further transactional findings omitted for brevity]")

    return "\n".join(lines)
