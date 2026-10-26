"""Condense verbose scope-area 'Summary of Risks' text into short Comments bullets via one LLM call.

Used by ScopeAreasBuilder for slides 9/10. One LLM call per scope area, run concurrently across all
areas. Any failure (or empty model output) falls back to the full text with bold markers stripped, so
the Comments cell is never lost — only shortened when the model succeeds.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage

from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from ..prompts.scope_comments_prompt import (
    SCOPE_COMMENTS_SYSTEM_PROMPT,
    SCOPE_COMMENTS_USER_TEMPLATE,
)

logger = get_logger(__name__)

_MISSING = "-"
_MAX_WORKERS = 3          # gentle concurrency so a burst of per-scope calls doesn't trip rate limits
_MAX_RETRIES = 2          # extra attempts (on top of the LLM client's own retries) for transient 429s
_RETRY_BACKOFF_S = 4


def _content_to_text(content) -> str:
    """Coerce an LLM message `.content` to text — it may be a str OR a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(part.get("text", "") or "")
        return "".join(parts)
    return str(content or "")


class ScopeCommentCondenser:
    """Rewrites each scope area's Summary of Risks into terse reference-style Comments bullets."""

    def __init__(self, config: ScopingDeckConfig) -> None:
        self._config = config
        self._llm = AzureOpenAILLMProvider(config.settings).get_client()

    def condense(self, scope_area: str, summary: str) -> str:
        """Return condensed Comments bullets for one scope area (full text on empty input/failure)."""
        full = (summary or "").replace("**", "").strip()
        if not full:
            return _MISSING
        messages = [
            SystemMessage(content=SCOPE_COMMENTS_SYSTEM_PROMPT),
            HumanMessage(content=SCOPE_COMMENTS_USER_TEMPLATE.format(
                scope_area=scope_area, summary=summary.strip(),
            )),
        ]
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 2):  # initial try + _MAX_RETRIES retries
            try:
                response = self._llm.invoke(messages)
                condensed = _content_to_text(response.content).replace("**", "").strip()
                if condensed:
                    return condensed
                logger.warning("[ScopeComments] Empty condensed output for '%s' — keeping full text",
                               scope_area)
                return full
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt <= _MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF_S * attempt)
                    continue
        # Exhausted retries — surface the real cause loudly (with traceback) so it is diagnosable.
        logger.warning("[ScopeComments] Condense FAILED for '%s' after %d attempt(s) — keeping full "
                       "text | %s", scope_area, _MAX_RETRIES + 1, last_exc, exc_info=last_exc)
        return full

    def condense_map(self, items: list[tuple[str, str]]) -> dict[int, str]:
        """Condense many (scope_area, summary) pairs concurrently; key the result by item index."""
        if not items:
            return {}
        results: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(items))) as pool:
            futures = {
                pool.submit(self.condense, scope_area, summary): idx
                for idx, (scope_area, summary) in enumerate(items)
            }
            for future in futures:
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:  # noqa: BLE001 — condense() already guards; belt-and-suspenders
                    scope_area, summary = items[idx]
                    logger.warning("[ScopeComments] Worker failed for '%s' | %s", scope_area, exc)
                    results[idx] = (summary or "").replace("**", "").strip() or _MISSING
        chars_in = sum(len((summary or "")) for _, summary in items)
        chars_out = sum(len(v) for v in results.values())
        pct = (100 * (chars_in - chars_out) / chars_in) if chars_in else 0.0
        logger.info("[ScopeComments] Condensed %d scope-area comment(s) | chars %d -> %d (%.0f%% shorter)",
                    len(results), chars_in, chars_out, pct)
        return results
