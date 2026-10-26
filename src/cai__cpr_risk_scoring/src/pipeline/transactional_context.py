"""Format Phase-3 transactional findings into a compact current-year context block.

Phase 3 runs first and produces per-scope transactional risk findings. This module renders them as
a bounded summary that is injected into every question's Phase-2 analysis, so the model reasons over
current-year transactional evidence alongside the response and supporting documents.
"""

from src.schemas.state import RiskOutput

_MAX_FINDINGS = 40
_MAX_DESCRIPTION_CHARS = 300
_MAX_CALCULATION_CHARS = 220

_HEADER = (
    "=== TRANSACTIONAL ANALYSIS (CURRENT-YEAR DATA) ===\n"
    "Potential risks identified in the current-year transactional data. Treat these as CURRENT-YEAR "
    "evidence: use them to confirm or rule out issues raised in the response, and to cross-validate "
    "GL-account and amount claims. A prior-year finding becomes a current-year risk only when it is "
    "corroborated here."
)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_transactional_context(findings: list[RiskOutput]) -> str:
    """Render Phase-3 findings as a compact, bounded current-year evidence block.

    Returns an empty string when there are no findings so callers can omit the section entirely.
    """
    if not findings:
        return ""

    lines = [_HEADER]
    for position, finding in enumerate(findings[:_MAX_FINDINGS], start=1):
        lines.append(
            f"[{position}] Scope: {finding.scope_area} | {finding.risk_type}"
        )
        lines.append(f"    {_truncate(finding.risk_description, _MAX_DESCRIPTION_CHARS)}")
        if finding.calculation_evidence:
            lines.append(
                f"    Calculation: {_truncate(finding.calculation_evidence, _MAX_CALCULATION_CHARS)}"
            )

    remaining = len(findings) - _MAX_FINDINGS
    if remaining > 0:
        lines.append(f"[+{remaining} further transactional findings omitted for brevity]")

    return "\n".join(lines)
