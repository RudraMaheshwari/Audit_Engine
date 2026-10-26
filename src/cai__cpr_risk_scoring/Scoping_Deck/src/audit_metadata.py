"""Audit metadata shown on the opening slide.

Sourced per run from the API (via env vars the Scoping Deck request sets); the previous hardcoded
Sam_1 values remain as fallbacks so a bare CLI run still works.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from src.config.settings import Settings


@dataclass(frozen=True)
class AuditMetadata:
    """The identifying details of an audit, rendered on the opening slide."""

    audit_entity: str
    mrc_number: str
    country: str
    iso3: str
    scope_period_start: date
    scope_period_end: date
    current_date: date

    @property
    def scope_period_display(self) -> str:
        return f"{self._format(self.scope_period_start)} to {self._format(self.scope_period_end)}"

    @property
    def current_date_display(self) -> str:
        return self._format(self.current_date)

    def as_context(self) -> dict[str, str]:
        """Return the template variables the opening slide binds to."""
        return {
            "audit_entity": self.audit_entity,
            "mrc_number": self.mrc_number,
            "scope_period": self.scope_period_display,
            "current_date": self.current_date_display,
        }

    @staticmethod
    def _format(value: date) -> str:
        return f"{value:%B} {value.day}, {value.year}"


class AuditMetadataProvider:
    """Provides the audit metadata for the current run."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def load(self) -> AuditMetadata:
        """Return the audit metadata — from the per-run env (API), with Sam_1 values as fallback."""
        def _env(name: str) -> str:
            return (os.getenv(name, "") or "").strip()

        def _date(name: str, default: date) -> date:
            raw = _env(name)
            if raw:
                try:
                    return date.fromisoformat(raw)
                except ValueError:
                    pass
            return default

        return AuditMetadata(
            audit_entity=_env("SCOPING_DECK_ENTITY_NAME") or self._settings.auditor_id or "IM Australia",
            mrc_number=_env("SCOPING_DECK_MRC_NUMBER") or "MRC1234",
            country=_env("SCOPING_DECK_COUNTRY") or "Australia",
            iso3=_env("SCOPING_DECK_ISO3") or "AUS",
            scope_period_start=_date("SCOPING_DECK_SCOPE_PERIOD_START", date(2024, 1, 1)),
            scope_period_end=_date("SCOPING_DECK_SCOPE_PERIOD_END", date(2024, 12, 31)),
            current_date=date.today(),
        )
