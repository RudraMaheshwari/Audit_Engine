"""Overview — fleet-level KPIs and trends across all observed runs (obs_run)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import theme
import ui
from data import lakebase_repo as lk


@ui.fragment
def render() -> None:
    ui.section("Overview", "Fleet-level cost, tokens and risk across every observed run.")
    with st.spinner("Loading runs…"):
        runs = lk.runs()
    if runs.empty:
        ui.empty_state("🗂️", "No runs recorded yet",
                       "Runs appear here once the pipeline has executed with OBS_ENABLED=true.")
        return

    runs["started_at"] = pd.to_datetime(runs["started_at"], errors="coerce")
    for c in ("total_cost_usd", "total_tokens", "total_input_tokens", "total_output_tokens",
              "total_llm_calls", "duration_ms"):
        if c in runs:
            runs[c] = pd.to_numeric(runs[c], errors="coerce")

    ok = int((runs["status"] == "completed").sum())
    ui.kpi_row([
        ("Runs", f"{len(runs):,}", f"{ok} completed"),
        ("Total cost", f"${runs['total_cost_usd'].fillna(0).sum():,.2f}", "USD, all runs"),
        ("Tokens", f"{int(runs['total_tokens'].fillna(0).sum()):,}", "input + output"),
        ("LLM calls", f"{int(runs.get('total_llm_calls', pd.Series(dtype=float)).fillna(0).sum()):,}", ""),
        ("Avg cost / run", f"${runs['total_cost_usd'].fillna(0).mean():,.2f}", ""),
    ])

    st.write("")
    left, right = st.columns([1.6, 1])
    with left:
        ui.panel_title("Cost per run")
        fig = px.bar(runs.sort_values("started_at"), x="started_at", y="total_cost_usd",
                     color="status", hover_data=["auditor_id"])
        st.plotly_chart(ui.themed(fig, 300), use_container_width=True)
    with right:
        ui.panel_title("Findings by severity")
        sev = {c.replace("findings_", "").replace("_", " ").title():
               int(pd.to_numeric(runs[c], errors="coerce").fillna(0).sum())
               for c in ("findings_high", "findings_medium", "findings_low", "findings_no_risk") if c in runs}
        dfs = pd.DataFrame({"severity": list(sev), "count": list(sev.values())})
        fig = px.pie(dfs, names="severity", values="count", hole=0.58,
                     color="severity", color_discrete_map={"High": "#ff4d4f", "Medium": "#ffa64d",
                                                           "Low": "#28c76f", "No Risk": "#7f8aa3"})
        st.plotly_chart(ui.themed(fig, 300), use_container_width=True)

    ui.panel_title("Token volume over time")
    tok = runs.sort_values("started_at")[["started_at", "total_input_tokens", "total_output_tokens"]]
    tok = tok.melt("started_at", var_name="kind", value_name="tokens")
    tok["kind"] = tok["kind"].str.replace("total_", "").str.replace("_tokens", "")
    st.plotly_chart(ui.themed(px.area(tok, x="started_at", y="tokens", color="kind"), 280),
                    use_container_width=True)

    ui.panel_title("Recent runs")
    st.dataframe(
        runs[["run_id", "auditor_id", "status", "started_at", "duration_ms",
              "total_cost_usd", "total_tokens", "total_llm_calls"]].head(50),
        use_container_width=True, hide_index=True,
    )
