"""Generate the 'Summary of Risks' story for a scope area.

Structure of the produced cell:
  Line 1 (only when the scope has transaction data): "Total transactions: N  |  Total amount: <X>"
          — the ONLY place exact figures appear; built deterministically from Output F.
  Story  : a short, audit-friendly narrative from the Output C Description(s) + Rationale(s), with the
          scope's transactional-spend share woven in. No exact numbers, no policy citations (LLM).
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.utils.logger import get_logger

from .output_f_loader import ScopeTxn
from .prompts.scope_summary_prompt import (
    SCOPE_SUMMARY_SYSTEM_PROMPT,
    SCOPE_SUMMARY_USER_TEMPLATE,
)
from .scope_aggregator import ScopeGroup

logger = get_logger(__name__)


def _context_block(group: ScopeGroup) -> str:
    """Description + Rationale per risk row (the story's source material)."""
    parts: list[str] = []
    for i, row in enumerate(group.rows, start=1):
        desc = (row.risk_description or "").strip()
        rat = (getattr(row, "risk_rationale", "") or "").strip()
        if not desc and not rat:
            continue
        chunk = f"{i}. {desc}"
        if rat:
            chunk += f"\n   Rationale: {rat}"
        parts.append(chunk)
    return "\n".join(parts)


def _totals_line(txn: ScopeTxn | None) -> str:
    """The exact first line, or '' when this scope has no transaction data. When the amount is blank
    (e.g. a name-list scope with no monetary column), show the transaction COUNT only — not a hanging
    'Total amount: -'."""
    if txn is None or not txn.total_txns:
        return ""
    amount = (txn.amount_display or "").strip()
    if not amount or amount in ("-", "—"):
        return f"Total transactions: {txn.total_txns:,}"
    return f"Total transactions: {txn.total_txns:,}  |  Total amount: {amount}"


def _share_line(txn: ScopeTxn | None, grand_total_usd: float) -> str:
    """Narrative hint for the LLM about this scope's share of transactional spend (percentage only)."""
    if txn is None or not txn.amount_usd or grand_total_usd <= 0:
        return "not a transactional scope / share not available — do not state a percentage"
    pct = round(txn.amount_usd / grand_total_usd * 100, 1)
    return f"about {pct}% of the audit's transactional spend"


def generate_summary(
    llm: BaseChatModel,
    group: ScopeGroup,
    txn: ScopeTxn | None = None,
    grand_total_usd: float = 0.0,
) -> str:
    """Return the storytelling 'Summary of Risks' for one scope area (totals line + narrative story)."""
    totals_line = _totals_line(txn)
    context = _context_block(group)
    if not context:
        logger.warning("[Summary] Scope %r has no description/rationale — totals-only", group.scope_area)
        return totals_line

    user_msg = SCOPE_SUMMARY_USER_TEMPLATE.format(
        scope_area=group.scope_area,
        overall_category=group.overall_risk_category or "(not categorized)",
        share_line=_share_line(txn, grand_total_usd),
        context_block=context,
    )
    try:
        response = llm.invoke([
            SystemMessage(content=SCOPE_SUMMARY_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        story = (response.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.error("[Summary] LLM story failed for scope %r | %s", group.scope_area, exc)
        story = ""

    if totals_line and story:
        return f"{totals_line}\n{story}"
    return totals_line or story
