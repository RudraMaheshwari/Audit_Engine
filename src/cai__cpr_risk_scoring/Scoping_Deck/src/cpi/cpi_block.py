"""Composes the CPI slide block text from a CpiResult.

The heading is wrapped in ** so the renderer bolds it; the data lines are prefixed with '- ' so the
renderer treats them as bullets. The intro is the fixed CPI explanation.
"""

from __future__ import annotations

from .models import CpiResult

_INTRO = (
    "The CPI ranks 180 countries and territories around the world by their perceived levels of "
    "public sector corruption, scoring on a scale of 0 (highly corrupt) to 100 (very clean). "
    "Additionally, each country is ranked on a scale of 1 (least corrupt) to 180 (most corrupt) "
    "and the ranking represents its position relative to the other countries in the index."
)


class CpiBlockComposer:
    """Turns a CpiResult into the slide-ready CPI text block."""

    def compose(self, result: CpiResult) -> str:
        heading = f'**Corruption Perceptions Index ("CPI") – {result.country}:**'
        lines = [heading, _INTRO]
        for figure in result.figures:
            lines.append(
                f"- {result.country} {figure.year}: Score of {figure.score}/100 "
                f"(perceived as {figure.perception}) and country rank of "
                f"{figure.rank}/{figure.total_countries}"
            )
        return "\n".join(lines)
