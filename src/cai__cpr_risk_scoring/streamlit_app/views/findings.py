"""Findings — risk distribution across all runs (obs_finding): by severity and by scope area."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import ui
from data import lakebase_repo as lk

_SEV = {"High Risk": "#ff4d4f", "Medium Risk": "#ffa64d", "Low Risk": "#28c76f",
        "No Risk": "#7f8aa3", "Not Applicable": "#9aa6c2", "(none)": "#9aa6c2"}


@ui.fragment
def render() -> None:
    ui.section("Findings", "Risk distribution across all runs — by severity and scope area.")
    with st.spinner("Loading findings…"):
        cat = lk.findings_by_category()
    if cat.empty:
        ui.empty_state("⚠️", "No findings recorded yet", "Findings appear here after a Risk Analysis run.")
        return
    cat["findings"] = pd.to_numeric(cat["findings"], errors="coerce")

    ui.kpi_row([
        ("Total findings", f"{int(cat['findings'].sum()):,}"),
        ("High", f"{int(cat.loc[cat['risk_category'] == 'High Risk', 'findings'].sum()):,}"),
        ("Medium", f"{int(cat.loc[cat['risk_category'] == 'Medium Risk', 'findings'].sum()):,}"),
        ("Categories", f"{cat['risk_category'].nunique()}"),
    ])

    st.write("")
    left, right = st.columns([1, 1.7])
    with left:
        ui.panel_title("By severity")
        st.plotly_chart(ui.themed(px.pie(cat, names="risk_category", values="findings", hole=0.58,
                                          color="risk_category", color_discrete_map=_SEV), 360),
                        use_container_width=True)
    with right:
        ui.panel_title("By scope area × severity (top 15)")
        scope = lk.findings_by_scope()
        scope["findings"] = pd.to_numeric(scope["findings"], errors="coerce")
        top = scope.groupby("scope_area")["findings"].sum().sort_values(ascending=False).head(15).index
        scope = scope[scope["scope_area"].isin(top)]
        fig = px.bar(scope, x="findings", y="scope_area", color="risk_category", orientation="h",
                     color_discrete_map=_SEV)
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(ui.themed(fig, 360), use_container_width=True)

    st.dataframe(cat.sort_values("findings", ascending=False), use_container_width=True, hide_index=True)
