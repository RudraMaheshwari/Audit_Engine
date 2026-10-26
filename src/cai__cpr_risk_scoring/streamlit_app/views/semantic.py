"""Semantic comparison — an output table vs its chat-edited `_copy` (reused for Risk Analysis + AI Scope).

Row-by-row cosine similarity of the two tables surfaces HOW MUCH the chat edits changed each row and
WHICH fields changed. Not blank before you run it: an intro card explains what to enter.
"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

import ui
from data import delta_repo as delta
from services import semantic_diff as sd

_STATUS = {"identical": "#28c76f", "minor": "#9be7b4", "modified": "#ff4d4f",
           "added": "#5b8cff", "removed": "#7f8aa3"}


@ui.fragment
def render(title: str, default_base: str, help_text: str) -> None:
    ui.section(title, help_text)

    with st.container(border=True):
        mode = st.radio("Pick tables by", ["Engagement number", "Full table names"],
                        horizontal=True, key=f"{default_base}_mode")
        if mode == "Engagement number":
            eng = st.text_input("Engagement number(s)", placeholder="e.g. 1010", key=f"{default_base}_eng")
            orig_fqn = delta.fqn(default_base, eng) if eng else ""
            copy_fqn = delta.fqn(default_base, eng, copy=True) if eng else ""
            c1, c2 = st.columns(2)
            c1.caption(f"original: `{orig_fqn or '—'}`")
            c2.caption(f"_copy: `{copy_fqn or '—'}`")
        else:
            orig_fqn = st.text_input("Original table (fully-qualified)", key=f"{default_base}_orig")
            copy_fqn = st.text_input("Edited _copy table (fully-qualified)", key=f"{default_base}_copy")
        threshold = st.slider("Semantic-change threshold (lower = stricter)", 0.80, 1.00,
                              sd.DEFAULT_SIM_THRESHOLD, 0.01, key=f"{default_base}_thr")
        go = st.button("Compare", type="primary", key=f"{default_base}_go", use_container_width=True)

    if not go:
        ui.empty_state("🔬", "Ready to compare",
                       "Enter an engagement number (auto-builds the table + its _copy) or two "
                       "fully-qualified table names, then press Compare.")
        return
    if not orig_fqn or not copy_fqn:
        st.warning("Provide both the original and the _copy table.")
        return

    try:
        with st.spinner("Reading tables…"):
            df_o, df_c = delta.read_table(orig_fqn), delta.read_table(copy_fqn)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read the tables: {exc}")
        return
    if df_o.empty or df_c.empty:
        st.warning("One of the tables is empty.")
        return

    key_col = st.selectbox("Row key column (to align rows)", list(df_o.columns), index=0,
                           key=f"{default_base}_key")
    with st.spinner("Embedding rows and scoring semantic change…"):
        result = sd.compare(df_o, df_c, key_col, threshold=threshold)
    summary, table, texts = result["summary"], result["table"], result["texts"]

    ui.kpi_row([
        ("Common rows", f"{summary['common']}"),
        ("Modified", f"{summary['modified']}"),
        ("Minor", f"{summary['minor']}"),
        ("Added / Removed", f"{summary['added']} / {summary['removed']}"),
        ("Mean similarity", f"{summary['mean_similarity']}" if summary["mean_similarity"] is not None else "—"),
    ])
    if summary["truncated"]:
        st.info(f"Embedded the first {summary['max_rows']} changed rows (table is larger).")

    st.write("")
    left, right = st.columns([1, 1.6])
    with left:
        ui.panel_title("Row status")
        counts = table["status"].value_counts().reset_index()
        counts.columns = ["status", "count"]
        st.plotly_chart(ui.themed(px.pie(counts, names="status", values="count", hole=0.58,
                                          color="status", color_discrete_map=_STATUS), 320),
                        use_container_width=True)
    with right:
        ui.panel_title("Similarity of changed rows (1.0 = unchanged)")
        changed = table[table["status"].isin(["modified", "minor"])]
        if changed.empty:
            ui.empty_state("✅", "No semantic changes", "The two tables are semantically identical.")
        else:
            st.plotly_chart(ui.themed(px.histogram(changed, x="similarity", nbins=20), 320),
                            use_container_width=True)

    ui.panel_title("Per-row status")
    st.dataframe(table.sort_values("similarity"), use_container_width=True, hide_index=True)

    modified = table.loc[table["status"] == "modified", "key"].tolist()
    if modified:
        ui.panel_title("🔍 What changed (field-level)")
        o_by = {str(r[key_col]): r for _, r in df_o.iterrows()}
        c_by = {str(r[key_col]): r for _, r in df_c.iterrows()}
        for k in modified:
            sim = table.loc[table["key"] == k, "similarity"].iloc[0]
            with st.expander(f"Row {k}  ·  similarity {sim}"):
                st.dataframe(sd.field_diff(o_by[k], c_by[k], key_col),
                             use_container_width=True, hide_index=True)
                ot, ct = texts[k]
                st.code(sd.inline_diff(ot, ct), language="diff")
