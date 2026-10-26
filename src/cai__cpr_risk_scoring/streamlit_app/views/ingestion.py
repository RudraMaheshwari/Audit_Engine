"""Ingestion — embedding/ingestion observability across runs (obs_ingestion)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import ui
from data import lakebase_repo as lk


@ui.fragment
def render() -> None:
    ui.section("Ingestion", "Files, chunks, tokens and cost across every ingestion run.")
    with st.spinner("Loading ingestion…"):
        by_status = lk.ingestion_by_status()
        by_index = lk.ingestion_by_index()
    if by_status.empty and by_index.empty:
        ui.empty_state("📥", "No ingestion rows yet", "Ingestion observability appears here after a run.")
        return

    for df in (by_status, by_index):
        for c in ("files", "chunks", "tokens", "cost_usd"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")

    ui.kpi_row([
        ("Files", f"{int(by_status['files'].sum()):,}"),
        ("Chunks", f"{int(by_status['chunks'].fillna(0).sum()):,}"),
        ("Cost", f"${by_status['cost_usd'].fillna(0).sum():,.2f}", "USD"),
        ("Indexes", f"{by_index['index_key'].nunique() if not by_index.empty else 0}"),
    ])

    st.write("")
    left, right = st.columns([1, 1.5])
    with left:
        ui.panel_title("Files by status")
        st.plotly_chart(ui.themed(px.pie(by_status, names="status", values="files", hole=0.58), 330),
                        use_container_width=True)
    with right:
        ui.panel_title("Chunks by index")
        st.plotly_chart(ui.themed(px.bar(by_index.sort_values("chunks", ascending=False),
                                          x="index_key", y="chunks", hover_data=["files", "tokens"]), 330),
                        use_container_width=True)

    st.dataframe(by_index, use_container_width=True, hide_index=True)
