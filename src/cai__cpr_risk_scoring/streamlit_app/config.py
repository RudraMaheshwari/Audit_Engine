"""Shared bootstrap for the Streamlit dashboard.

Reuses the MAIN app's environment, settings and credentials — the project root is put on sys.path so
`src.*` imports resolve, `.env` + Key Vault load exactly as the API does, and the same `Settings`
object feeds Lakebase (observability) and the Databricks SQL warehouse (Delta tables).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable so the main app's `src` package resolves regardless of CWD.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st


@st.cache_resource(show_spinner=False)
def get_settings():
    """The main app's Settings (env + Key Vault), loaded once per session."""
    try:
        from src.config.config_loader import load_config

        load_config()
    except Exception:  # noqa: BLE001 — fall back to plain .env if the loader/vault is unavailable
        from dotenv import load_dotenv

        load_dotenv()
    from src.config.settings import get_settings as _get_settings

    return _get_settings()


@st.cache_resource(show_spinner=False)
def get_sql_engine():
    """A cached SQLAlchemy engine for the Databricks SQL warehouse (Delta-table reads)."""
    from src.config.databricks_auth import build_engine

    return build_engine(get_settings(), pool_pre_ping=True)


def output_schema() -> str:
    return getattr(get_settings(), "risk_analysis_output_schema", "risk_analysis_output")


def catalog() -> str:
    return getattr(get_settings(), "databricks_catalog", "")
