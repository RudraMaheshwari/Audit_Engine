"""Read-only queries against the Lakebase observability tables → pandas DataFrames.

Uses the main app's `LakebaseConnection` (same auth/host resolution). Every query runs through
`_query_df`, which opens a fresh psycopg connection (credentials minted per connect), reads via the
DBAPI cursor (robust with psycopg3), and closes it. Results are cached briefly so flipping between
tabs doesn't re-hit the DB on every rerun.

Table + schema names come from `src.config.constants` so there is a single source of truth with the
recorder: public.obs_run / obs_question / obs_llm_call / obs_finding / obs_ingestion.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import get_settings
from src.config.constants import (
    OBS_SCHEMA,
    OBS_TABLE_FINDING,
    OBS_TABLE_INGESTION,
    OBS_TABLE_LLM_CALL,
    OBS_TABLE_QUESTION,
    OBS_TABLE_RUN,
)

RUN = f"{OBS_SCHEMA}.{OBS_TABLE_RUN}"
QUESTION = f"{OBS_SCHEMA}.{OBS_TABLE_QUESTION}"
LLM_CALL = f"{OBS_SCHEMA}.{OBS_TABLE_LLM_CALL}"
FINDING = f"{OBS_SCHEMA}.{OBS_TABLE_FINDING}"
INGESTION = f"{OBS_SCHEMA}.{OBS_TABLE_INGESTION}"


@st.cache_data(ttl=60, show_spinner=False)
def _query_df(sql: str, params: tuple | None = None) -> pd.DataFrame:
    from src.observability.lakebase import LakebaseConnection

    conn = LakebaseConnection(get_settings()).open()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


def driver_ok() -> bool:
    from src.observability.lakebase import LakebaseConnection

    return LakebaseConnection.driver_available()


# ── Runs ──────────────────────────────────────────────────────────────────────
def runs(limit: int = 500) -> pd.DataFrame:
    return _query_df(f"SELECT * FROM {RUN} ORDER BY started_at DESC LIMIT %s", (limit,))


def run_ids(limit: int = 500) -> pd.DataFrame:
    return _query_df(
        f"SELECT run_id, auditor_id, status, started_at, total_cost_usd, total_tokens "
        f"FROM {RUN} ORDER BY started_at DESC LIMIT %s",
        (limit,),
    )


# ── Per-run detail ──────────────────────────────────────────────────────────────
def questions_for(run_id: str) -> pd.DataFrame:
    return _query_df(f"SELECT * FROM {QUESTION} WHERE run_id = %s ORDER BY question_index", (run_id,))


def llm_calls_for(run_id: str) -> pd.DataFrame:
    return _query_df(f"SELECT * FROM {LLM_CALL} WHERE run_id = %s ORDER BY started_at", (run_id,))


def findings_for(run_id: str) -> pd.DataFrame:
    return _query_df(f"SELECT * FROM {FINDING} WHERE run_id = %s", (run_id,))


def ingestion_for(run_id: str) -> pd.DataFrame:
    return _query_df(f"SELECT * FROM {INGESTION} WHERE run_id = %s ORDER BY started_at", (run_id,))


# ── Global aggregates ────────────────────────────────────────────────────────────
def llm_calls_by(column: str) -> pd.DataFrame:
    """Aggregate LLM calls by stage / agent_name / model (column is validated against a whitelist)."""
    allowed = {"stage", "agent_name", "model", "prompt_name", "scope_area"}
    col = column if column in allowed else "stage"
    return _query_df(
        f"SELECT COALESCE({col}, '(none)') AS {col}, COUNT(*) AS calls, "
        f"SUM(total_tokens) AS tokens, SUM(cost_usd) AS cost_usd, "
        f"AVG(latency_ms) AS avg_latency_ms, "
        f"SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors "
        f"FROM {LLM_CALL} GROUP BY 1 ORDER BY cost_usd DESC NULLS LAST"
    )


def findings_by_category() -> pd.DataFrame:
    return _query_df(
        f"SELECT COALESCE(risk_category, '(none)') AS risk_category, COUNT(*) AS findings "
        f"FROM {FINDING} GROUP BY 1 ORDER BY findings DESC"
    )


def findings_by_scope() -> pd.DataFrame:
    return _query_df(
        f"SELECT COALESCE(scope_area, '(none)') AS scope_area, "
        f"COALESCE(risk_category, '(none)') AS risk_category, COUNT(*) AS findings "
        f"FROM {FINDING} GROUP BY 1, 2 ORDER BY findings DESC"
    )


def ingestion_by_status() -> pd.DataFrame:
    return _query_df(
        f"SELECT COALESCE(status, '(none)') AS status, COUNT(*) AS files, "
        f"SUM(num_chunks) AS chunks, SUM(cost_usd) AS cost_usd "
        f"FROM {INGESTION} GROUP BY 1 ORDER BY files DESC"
    )


def ingestion_by_index() -> pd.DataFrame:
    return _query_df(
        f"SELECT COALESCE(index_key, '(none)') AS index_key, COUNT(*) AS files, "
        f"SUM(num_chunks) AS chunks, SUM(total_tokens) AS tokens, SUM(cost_usd) AS cost_usd "
        f"FROM {INGESTION} GROUP BY 1 ORDER BY files DESC"
    )
