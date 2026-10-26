"""AI fallback for the proposed sample size — used ONLY when the CPR guidance workbook has none.

The guidance workbook is always authoritative. This module is invoked solely when
GuidanceSampleSizes.lookup() returns None for a scope area, so a reviewer still gets a reasonable,
risk-based starting number instead of a blank cell. Best-effort: returns None on any failure so the
cell simply stays blank (the prior behaviour).
"""

from __future__ import annotations

import re

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.utils.logger import get_logger

from .prompts.sample_size_prompt import (
    SAMPLE_SIZE_SYSTEM_PROMPT,
    SAMPLE_SIZE_USER_TEMPLATE,
)
from .scope_aggregator import ScopeGroup

logger = get_logger(__name__)

_MIN_SAMPLE = 1
_MAX_SAMPLE = 60


def _descriptions_block(group: ScopeGroup) -> str:
    parts: list[str] = []
    for i, row in enumerate(group.rows, start=1):
        desc = (row.risk_description or "").strip()
        if desc:
            parts.append(f"{i}. {desc}")
    return "\n".join(parts)


def suggest_sample_size(llm: BaseChatModel, group: ScopeGroup) -> int | None:
    """Suggest a risk-based sample size for a scope area with no guidance value. None on failure."""
    descriptions = _descriptions_block(group)
    user_msg = SAMPLE_SIZE_USER_TEMPLATE.format(
        scope_area=group.scope_area,
        risk_category=group.overall_risk_category or "(not applicable)",
        risk_count=len(group.rows),
        descriptions_block=descriptions or "(no descriptions available)",
    )
    try:
        resp = llm.invoke([
            SystemMessage(content=SAMPLE_SIZE_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        m = re.search(r"\d+", resp.content or "")
        if not m:
            logger.warning("[SampleSize] LLM returned no integer for scope %r — leaving blank", group.scope_area)
            return None
        n = int(m.group())
        n = max(_MIN_SAMPLE, min(_MAX_SAMPLE, n))  # clamp to sensible bounds
        logger.info("[SampleSize] AI-suggested sample size for scope %r = %d (no guidance value)", group.scope_area, n)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SampleSize] Suggestion failed for scope %r — leaving blank | %s", group.scope_area, exc)
        return None
