"""Value objects for Corruption Perceptions Index (CPI) figures."""

from __future__ import annotations

from dataclasses import dataclass

_LESS_CORRUPT_THRESHOLD = 50


@dataclass(frozen=True)
class CpiYearFigure:
    """A country's CPI score and rank for one year."""

    year: int
    score: int
    rank: int
    total_countries: int

    @property
    def perception(self) -> str:
        """'less corrupt' for a score above 50, otherwise 'more corrupt'."""
        return "less corrupt" if self.score > _LESS_CORRUPT_THRESHOLD else "more corrupt"


@dataclass(frozen=True)
class CpiResult:
    """A country's CPI figures for the two most recent published years (newest first)."""

    country: str
    figures: tuple[CpiYearFigure, ...]
