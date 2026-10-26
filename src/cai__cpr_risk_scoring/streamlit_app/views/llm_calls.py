"""LLM analytics — cost / tokens / latency / errors sliced by stage, agent, and model (all runs)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import ui
from data import lakebase_repo as lk


def _num(df: pd.DataFrame) -> pd.DataFrame:
    for c in ("calls", "tokens", "cost_usd", "avg_latency_ms", "errors"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@ui.fragment
def render() -> None:
    ui.section("LLM analytics", "Cost, tokens, latency and errors across every model call.")
    dim = st.segmented_control("Group by", ["stage", "agent_name", "model", "prompt_name", "scope_area"],
                               default="stage") if hasattr(st, "segmented_control") else \
        st.radio("Group by", ["stage", "agent_name", "model", "prompt_name", "scope_area"], horizontal=True)
    dim = dim or "stage"

    with st.spinner("Aggregating LLM calls…"):
        df = _num(lk.llm_calls_by(dim))
    if df.empty:
        ui.empty_state("🤖", "No LLM-call rows yet", "Model-call observability appears here after a run.")
        return

    ui.kpi_row([
        ("Calls", f"{int(df['calls'].sum()):,}"),
        ("Cost", f"${df['cost_usd'].fillna(0).sum():,.2f}", "USD"),
        ("Tokens", f"{int(df['tokens'].fillna(0).sum()):,}"),
        ("Errors", f"{int(df['errors'].fillna(0).sum()):,}"),
    ])

    st.write("")
    left, right = st.columns(2)
    with left:
        ui.panel_title(f"Cost by {dim}")
        st.plotly_chart(ui.themed(px.bar(df, x=dim, y="cost_usd", hover_data=["calls", "tokens"]), 340),
                        use_container_width=True)
    with right:
        ui.panel_title(f"Avg latency by {dim} (ms)")
        st.plotly_chart(ui.themed(px.bar(df.sort_values("avg_latency_ms", ascending=False), x=dim,
                                         y="avg_latency_ms", color="avg_latency_ms",
                                         color_continuous_scale="Tealrose"), 340),
                        use_container_width=True)

    st.dataframe(df, use_container_width=True, hide_index=True)
