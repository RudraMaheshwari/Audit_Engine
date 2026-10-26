"""Builds the row data for slide 9 (Areas in Scope) and slide 10 (Areas not in Scope).

Source of truth is the AI Scope workbook (scope area, decision, sample size, summary). Scope areas with
Scope Decision == "Included" go to slide 9; everything else (default "Excluded") goes to slide 10.

Transaction totals (count + USD) are loaded best-effort from Output F (Scope Txn Summary) in the main
audit workbook — the same file as Output E. When Output F is unavailable or a scope area is not found,
the values fall back to "-" per spec.
"""

from __future__ import annotations

import re

from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from .comment_condenser import ScopeCommentCondenser
from .output_f_loader import load_output_f_totals
from .reader import ScopeAreaRow, load_scope_areas

logger = get_logger(__name__)

_MISSING = "-"


def _norm_scope(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _or_missing(value: str) -> str:
    return value.strip() if value and value.strip() else _MISSING


def _full_comment(text: str) -> str:
    """Show the Summary of Risks verbatim in the Comments column — NO trimming.

    Keeps the full multi-line / bulleted text (bullets and line breaks preserved); only markdown bold
    markers are stripped so they don't render as literal '**'. Returns "-" when empty.
    """
    if not text or not text.strip():
        return _MISSING
    return text.replace("**", "").strip()


class ScopeAreasBuilder:
    """Produces the slide-9 (in-scope) and slide-10 (excluded) table rows from the AI Scope workbook."""

    def __init__(self, config: ScopingDeckConfig) -> None:
        self._config = config
        # Lazy-loaded cache of Output F totals: {normalized_scope_area: (count, usd)}.
        self._output_f_cache: dict[str, tuple[str, str]] | None = None

    def build(self) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
        """Return (in_scope_rows, excluded_rows). Empty lists when the workbook is absent → slides skip."""
        rows = load_scope_areas(
            self._config.risk_scope_path, self._config.risk_scope_sheet,
            source=self._config.risk_scope_source, settings=self._config.settings,
        )
        if not rows:
            return [], []

        in_scope = [r for r in rows if r.is_included]
        excluded = [r for r in rows if not r.is_included]

        # Condense every scope area's verbose Summary of Risks into short reference-style Comments
        # bullets (one LLM call per area, run concurrently). Keyed by id(row) so each row keeps its own.
        comments = self._condense_comments(rows)

        in_rows = [self._in_scope_row(r, comments[id(r)]) for r in in_scope]
        ex_rows = [self._excluded_row(r, comments[id(r)]) for r in excluded]
        logger.info(
            "[ScopeAreas] Built slide rows | in_scope=%d | excluded=%d", len(in_rows), len(ex_rows),
        )
        return in_rows, ex_rows

    def _condense_comments(self, rows: list[ScopeAreaRow]) -> dict[int, str]:
        """Return {id(row): condensed Comments}. Falls back to the full text on any LLM failure."""
        try:
            condenser = ScopeCommentCondenser(self._config)
        except Exception as exc:  # noqa: BLE001 — never block the deck on LLM init
            logger.warning("[ScopeAreas] Comment condenser unavailable — using full text | %s", exc)
            return {id(r): _full_comment(r.summary_of_risks) for r in rows}

        items = [(r.scope_area, r.summary_of_risks) for r in rows]
        condensed = condenser.condense_map(items)
        return {
            id(r): (condensed.get(idx) or _full_comment(r.summary_of_risks))
            for idx, r in enumerate(rows)
        }

    def _in_scope_row(self, r: ScopeAreaRow, comment: str) -> tuple[str, ...]:
        # Columns: Area | Full/Limited | Sample Size | Total no. of txns | Total amount (USD) | Comments
        count, usd = self._transaction_totals(r.scope_area)
        return (
            r.scope_area,
            "Full",
            _or_missing(r.proposed_sample_size),
            count,
            usd,
            comment,
        )

    def _excluded_row(self, r: ScopeAreaRow, comment: str) -> tuple[str, ...]:
        # Columns: Area | Total no. of txns | Total amount (USD) | Comments
        count, usd = self._transaction_totals(r.scope_area)
        return (r.scope_area, count, usd, comment)

    def _ensure_output_f(self) -> dict[str, tuple[str, str]]:
        """Load Output F totals once and cache them for all scope-area lookups."""
        if self._output_f_cache is None:
            self._output_f_cache = load_output_f_totals(self._config)
        return self._output_f_cache

    def _transaction_totals(self, scope_area: str) -> tuple[str, str]:
        """Per-scope (transaction count, USD total) from Output F.

        Looks up the scope area in the Output F data (loaded from the main audit workbook).
        Falls back to ("-", "-") when Output F is unavailable or the scope area has no entry.
        """
        totals = self._ensure_output_f()
        key = _norm_scope(scope_area)
        if key in totals:
            return totals[key]
        logger.debug("[ScopeAreas] No Output F entry for scope area '%s' — using '-'", scope_area)
        return _MISSING, _MISSING
