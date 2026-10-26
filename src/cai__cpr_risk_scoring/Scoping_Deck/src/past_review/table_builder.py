"""Builds the CPR / GACS slide tables from prior-review observations.

Fetches all observations of a kind, groups them by scope area, and condenses every scope to at most
two concise bullets in a single LLM call so the whole table fits one slide (like the Business
Overview slide). Falls back to the first findings if the model call fails.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from ..market_overview.reference_library import ReferenceLibrary
from ..prompts.past_review_prompt import (
    PAST_REVIEW_CONDENSE_SYSTEM_PROMPT,
    PAST_REVIEW_CONDENSE_USER_TEMPLATE,
)
from .index_manager import PastReviewIndexManager
from .models import PastReviewObservation, ReviewKind

logger = get_logger(__name__)

# Fewer rows -> allow more bullets per scope so rows are informative; many rows -> keep it tight so
# the whole table still fits one slide.
_FEW_ROWS_THRESHOLD = 6
_MAX_BULLETS_FEW_ROWS = 3
_MAX_BULLETS_MANY_ROWS = 2


@dataclass(frozen=True)
class PastReviewTable:
    """Rows (scope area, bullet body) and the report year for a CPR/GACS slide."""

    rows: tuple[tuple[str, str], ...]
    year: int | None

    @property
    def is_empty(self) -> bool:
        return not self.rows


class PastReviewTableBuilder:
    """Turns prior-review observations into grouped, slide-sized table rows."""

    def __init__(self, config: ScopingDeckConfig, index: PastReviewIndexManager,
                 llm: BaseChatModel) -> None:
        self._config = config
        self._index = index
        self._llm = llm
        self._references = {
            ReviewKind.CPR: ReferenceLibrary(config.cpr_references_dir),
            ReviewKind.GACS: ReferenceLibrary(config.gacs_references_dir),
        }

    def build(self, kind: ReviewKind) -> PastReviewTable:
        observations = self._index.fetch_by_kind(self._config.settings.auditor_id, kind)
        if not observations:
            return PastReviewTable(rows=(), year=None)

        year = max((o.report_year for o in observations if o.report_year), default=None)
        groups = self._group_by_scope(observations)
        max_bullets = (
            _MAX_BULLETS_FEW_ROWS if len(groups) <= _FEW_ROWS_THRESHOLD else _MAX_BULLETS_MANY_ROWS
        )
        condensed = self._condense(groups, kind, max_bullets)
        rows_list = []
        for scope, items in groups.items():
            entry = condensed.get(scope) or {}
            # Display label = full form inferred from the findings by the LLM; raw scope if none.
            label = entry.get("label") or scope
            bullets = entry.get("bullets") or self._fallback(items, max_bullets)
            rows_list.append((label, "\n".join(f"- {b}" for b in bullets)))
        return PastReviewTable(rows=tuple(rows_list), year=year)

    @staticmethod
    def _group_by_scope(
        observations: list[PastReviewObservation],
    ) -> dict[str, list[PastReviewObservation]]:
        groups: dict[str, list[PastReviewObservation]] = {}
        for obs in observations:
            groups.setdefault(PastReviewTableBuilder._top_level_scope(obs.scope_area), []).append(obs)
        return groups

    @staticmethod
    def _top_level_scope(scope_area: str) -> str:
        """Collapse granular scopes to their top-level group (e.g. 'FFS - Local Monitoring' -> 'FFS').

        Splits only on a spaced dash so multi-part names like 'Fee-for-Service' stay intact. The label
        is kept raw here; abbreviations are spelled out later by the LLM condense step, which infers the
        full form from the findings text rather than any fixed lookup.
        """
        scope = (scope_area or "").strip()
        for separator in (" – ", " - "):
            if separator in scope:
                scope = scope.split(separator)[0].strip()
                break
        return scope or "Unspecified"

    def _condense(
        self, groups: dict[str, list[PastReviewObservation]], kind: ReviewKind, max_bullets: int
    ) -> dict[str, list[str]]:
        scopes_block = "\n".join(
            f"## {scope}\n" + "\n".join(f"- {self._format(o)}" for o in items)
            for scope, items in groups.items()
        )
        user = PAST_REVIEW_CONDENSE_USER_TEMPLATE.format(
            review_kind=kind.value.upper(),
            scopes_block=scopes_block,
            reference_block=self._references[kind].render_block(),
        )
        try:
            response = self._llm.invoke([
                SystemMessage(content=PAST_REVIEW_CONDENSE_SYSTEM_PROMPT.format(max_bullets=max_bullets)),
                HumanMessage(content=user),
            ])
            parsed = self._parse(response.content)
        except Exception as exc:
            logger.warning("[PastReview] Condense failed — using fallback | %s", exc)
            return {}
        out: dict[str, dict] = {}
        for scope, value in parsed.items():
            if isinstance(value, dict):
                bullets = value.get("bullets")
                out[scope] = {
                    "label": str(value.get("label") or "").strip(),
                    "bullets": [str(b).strip() for b in bullets][:max_bullets]
                    if isinstance(bullets, list) else [],
                }
            elif isinstance(value, list):  # tolerate the older bullets-only shape
                out[scope] = {"label": "", "bullets": [str(b).strip() for b in value][:max_bullets]}
        return out

    def _fallback(self, items: list[PastReviewObservation], max_bullets: int) -> list[str]:
        return [self._format(o) for o in items[:max_bullets]]

    @staticmethod
    def _format(obs: PastReviewObservation) -> str:
        if obs.criticality and obs.criticality.lower() not in obs.issue_description.lower():
            return f"{obs.criticality}: {obs.issue_description}"
        return obs.issue_description

    @staticmethod
    def _parse(content: str) -> dict:
        raw = (content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
