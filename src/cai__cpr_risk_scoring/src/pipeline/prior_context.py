"""Format past-audit and SI/Legal knowledge into a compact recurrence-context block.

Past-audit observations and SI History / Legal inputs are historical context, not current-year
risks. This block is injected into Phase-2 so the model can check whether a current-year finding
recurs from a prior audit or a known SI/Legal issue — without treating the prior item as a
confirmed current-year risk on its own.
"""

from src.models.schemas import DocumentChunk

_MAX_ITEMS_PER_SOURCE = 25
_MAX_CHARS = 300

_HEADER = (
    "=== PRIOR CONTEXT (HISTORICAL — NOT A CURRENT-YEAR RISK) ===\n"
    "Past-audit observations and SI History / Legal inputs below are HISTORICAL context. Use them "
    "only to check whether a current-year finding recurs; never conclude a current-year risk from a "
    "prior item alone — it must be corroborated by current-year evidence."
)


def _truncate(text: str) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= _MAX_CHARS else text[: _MAX_CHARS - 1].rstrip() + "…"


def _render_section(title: str, chunks: list[DocumentChunk]) -> list[str]:
    if not chunks:
        return []
    lines = [f"-- {title} --"]
    for position, chunk in enumerate(chunks[:_MAX_ITEMS_PER_SOURCE], start=1):
        lines.append(f"[{position}] {_truncate(chunk.content)}")
    return lines


def format_prior_context(
    past_audit_chunks: list[DocumentChunk],
    si_legal_chunks: list[DocumentChunk],
    additional_input_chunks: list[DocumentChunk] | None = None,
) -> str:
    """Render past-audit + SI/Legal + Additional Input chunks as a bounded recurrence-context block.

    Returns an empty string when there is nothing to show so callers can omit the section.
    """
    sections = _render_section("Past Audit Observations", past_audit_chunks)
    sections += _render_section("SI History & Legal Inputs", si_legal_chunks)
    sections += _render_section("Additional Inputs", additional_input_chunks or [])
    if not sections:
        return ""
    return "\n".join([_HEADER, *sections])
