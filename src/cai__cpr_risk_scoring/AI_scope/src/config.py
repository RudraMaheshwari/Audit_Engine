"""AI Scope configuration.

Reuses the main app's `Settings` (same .env / Databricks / Azure creds) and adds the AI-Scope knobs,
driven by env vars:

  AI_SCOPE_SOURCE        "excel" or "db_table" — where Output C is read from.
  AI_SCOPE_PATH          interpreted by source:
                           - excel    -> local path to the generated audit workbook (.xlsx)
                           - db_table -> fully-qualified Delta table holding Output C
  AI_SCOPE_OUTPUT_E_PATH (optional, db_table source only) fully-qualified Delta table holding Output E
                         (Past Audit Report). When omitted in db_table mode, past-audit risks are skipped.
                         Ignored for the excel source (Output E is read from the same workbook).
  AI_SCOPE_OUTPUT_F_PATH (optional, db_table source only) fully-qualified Delta table holding Output F
                         (Scope Txn Summary). When omitted in db_table mode it is auto-derived from the
                         Output C table's catalog.schema. Ignored for the excel source.
  AI_SCOPE_GUIDANCE_PATH (optional) override for the CPR guidance workbook that supplies the
                         per-scope proposed sample size. Defaults to settings.scope_area_policy_file_path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.config.settings import Settings, get_settings

SOURCE_EXCEL = "excel"
SOURCE_DB_TABLE = "db_table"

_DB_ALIASES = {"db_table", "db", "delta", "databricks", "table"}
_EXCEL_ALIASES = {"excel", "xlsx", "file", "local"}


@dataclass
class AIScopeConfig:
    settings: Settings
    source: str            # SOURCE_EXCEL | SOURCE_DB_TABLE
    path: str              # excel file path or DB table FQN
    guidance_path: str     # CPR guidance workbook for sample sizes
    output_e_path: str = ""  # Output E (Past Audit) DB table FQN — db_table source only; '' = skip
    output_f_path: str = ""  # Output F (Scope Txn) DB table FQN — db_table source only; '' = derive

    @staticmethod
    def _normalise_source(raw: str) -> str:
        s = (raw or "").strip().lower()
        if s in _EXCEL_ALIASES:
            return SOURCE_EXCEL
        if s in _DB_ALIASES:
            return SOURCE_DB_TABLE
        raise ValueError(
            f"AI_SCOPE_SOURCE must be one of 'excel' or 'db_table' (got {raw!r})"
        )


def load_config() -> AIScopeConfig:
    settings = get_settings()

    source = AIScopeConfig._normalise_source(os.getenv("AI_SCOPE_SOURCE", ""))

    path = (os.getenv("AI_SCOPE_PATH", "") or "").strip()
    if not path:
        raise ValueError("AI_SCOPE_PATH is not set — provide the Output C excel path or DB table.")

    output_e_path = (os.getenv("AI_SCOPE_OUTPUT_E_PATH", "") or "").strip()
    output_f_path = (os.getenv("AI_SCOPE_OUTPUT_F_PATH", "") or "").strip()
    guidance_path = (
        (os.getenv("AI_SCOPE_GUIDANCE_PATH", "") or "").strip()
        or settings.scope_area_policy_file_path
    )

    return AIScopeConfig(
        settings=settings,
        source=source,
        path=path,
        guidance_path=guidance_path,
        output_e_path=output_e_path,
        output_f_path=output_f_path,
    )
