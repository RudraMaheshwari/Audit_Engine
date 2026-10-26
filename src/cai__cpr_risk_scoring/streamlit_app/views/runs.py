"""Run detail — drill into one run: questions, LLM calls, findings, ingestion (trace-style)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import ui
from data import lakebase_repo as lk


@ui.fragment
def render() -> None:
    ui.section("Run detail", "Drill into a single run — questions, LLM calls, findings and ingestion.")
    ids = lk.run_ids()
    if ids.empty:
        ui.empty_state("🧵", "No runs yet", "A run must complete before its trace can be inspected.")
        return

    ids["label"] = (ids["started_at"].astype(str) + "  ·  " + ids["auditor_id"].astype(str)
                    + "  ·  " + ids["status"].astype(str) + "  ·  " + ids["run_id"].astype(str).str.slice(0, 8))
    run_id = str(ids.loc[ids["label"] == st.selectbox("Run", ids["label"].tolist()), "run_id"].iloc[0])

    tabs = st.tabs(["Questions", "LLM calls", "Findings", "Ingestion"])

    with tabs[0]:
        q = lk.questions_for(run_id)
        if q.empty:
            ui.empty_state("❓", "No question rows", "This run recorded no per-question observability.")
        else:
            ui.kpi_row([
                ("Questions", f"{len(q)}"),
                ("Errored", f"{int((q.get('status') == 'error').sum()) if 'status' in q else 0}"),
                ("Cost", f"${pd.to_numeric(q.get('cost_usd'), errors='coerce').fillna(0).sum():.3f}"),
            ])
            if "final_risk_category" in q:
                d = q["final_risk_category"].fillna("(none)").value_counts().reset_index()
                d.columns = ["final_risk_category", "count"]
                st.plotly_chart(ui.themed(px.bar(d, x="final_risk_category", y="count"), 260),
                                use_container_width=True)
            st.dataframe(q, use_container_width=True, hide_index=True)

    with tabs[1]:
        c = lk.llm_calls_for(run_id)
        if c.empty:
            ui.empty_state("🤖", "No LLM-call rows", "No model calls were recorded for this run.")
        else:
            ui.kpi_row([
                ("Calls", f"{len(c)}"),
                ("Tokens", f"{int(pd.to_numeric(c.get('total_tokens'), errors='coerce').fillna(0).sum()):,}"),
                ("Cost", f"${pd.to_numeric(c.get('cost_usd'), errors='coerce').fillna(0).sum():.3f}"),
            ])
            if "stage" in c:
                g = c.assign(cost=pd.to_numeric(c.get("cost_usd"), errors="coerce")) \
                     .groupby(c["stage"].fillna("(none)")).agg(calls=("stage", "size"),
                                                               cost=("cost", "sum")).reset_index()
                st.plotly_chart(ui.themed(px.bar(g, x="stage", y="cost", hover_data=["calls"]), 260),
                                use_container_width=True)
            st.dataframe(c, use_container_width=True, hide_index=True)

    with tabs[2]:
        f = lk.findings_for(run_id)
        if f.empty:
            ui.empty_state("⚠️", "No findings", "This run produced no findings.")
        else:
            if "risk_category" in f:
                d = f["risk_category"].fillna("(none)").value_counts().reset_index()
                d.columns = ["risk_category", "count"]
                st.plotly_chart(ui.themed(px.bar(d, x="risk_category", y="count", color="risk_category"), 260),
                                use_container_width=True)
            st.dataframe(f, use_container_width=True, hide_index=True)

    with tabs[3]:
        g = lk.ingestion_for(run_id)
        if g.empty:
            ui.empty_state("📥", "No ingestion rows", "No files were ingested in this run.")
        else:
            st.dataframe(g, use_container_width=True, hide_index=True)
