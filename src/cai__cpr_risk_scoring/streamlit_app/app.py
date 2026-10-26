"""AI Auditor — Observability dashboard (Streamlit).

Website-style top navigation, dark/light switching, card UI, themed charts, and fragment-based views
over the Lakebase observability tables + two semantic-comparison screens (Risk Analysis, AI Scope).
Reuses the main app's env / settings / credentials via `config.py`.

Run (from the project root):  streamlit run streamlit_app/app.py
"""

from __future__ import annotations

import streamlit as st

# config imports first — it puts the project root on sys.path for `src.*`, `data`, `views`, `services`.
from config import get_settings
import theme
import ui
from data import lakebase_repo as lk
from views import findings, ingestion, llm_calls, overview, runs, semantic

st.set_page_config(page_title="AI Auditor — Observability", page_icon="📊",
                   layout="wide", initial_sidebar_state="collapsed")

_NAV = {
    "📊 Overview": lambda: overview.render(),
    "🧵 Runs": lambda: runs.render(),
    "🤖 LLM": lambda: llm_calls.render(),
    "⚠️ Findings": lambda: findings.render(),
    "📥 Ingestion": lambda: ingestion.render(),
    "🔬 Risk Analysis": lambda: semantic.render(
        "Semantic — Risk Analysis", "AI_Risk_Summary",
        "Compare Output C (AI Risk Summary) against its chat-edited _copy to see what reviewers changed."),
    "🔬 AI Scope": lambda: semantic.render(
        "Semantic — AI Scope", "AI_Scope",
        "Compare the AI Scope table against its chat-edited _copy to see what reviewers changed."),
}


def _topbar() -> str:
    """Brand + nav + theme toggle + refresh, rendered as a single top bar (bordered card)."""
    settings = get_settings()
    bar = st.container(border=True)
    left, mid, right = bar.columns([3.0, 6.5, 2.2], vertical_alignment="center")
    with left:
        st.markdown(
            '<div class="brand"><span class="dot"></span>AI Auditor '
            '<small>· Observability</small></div>', unsafe_allow_html=True)
    with mid:
        choice = ui.top_nav(list(_NAV))
    with right:
        a, b = st.columns(2)
        with a:
            dark = theme.current() == "dark"
            if st.button("☀️ Light" if dark else "🌙 Dark", use_container_width=True, key="themebtn"):
                theme.toggle()
                st.rerun()
        with b:
            if st.button("↻ Refresh", use_container_width=True, key="refreshbtn"):
                st.cache_data.clear()
                st.rerun()
    cat = getattr(settings, "databricks_catalog", "") or "—"
    inst = getattr(settings, "lakebase_instance_name", "") or "—"
    st.caption(f"Lakebase `{inst}`  ·  Catalog `{cat}`"
               + ("" if lk.driver_ok() else "  ·  ⚠️ psycopg driver missing — obs queries will fail"))
    return choice


def main() -> None:
    theme.inject_css()
    choice = _topbar()
    try:
        _NAV[choice]()
    except Exception as exc:  # noqa: BLE001 — surface errors in-page instead of a blank screen
        st.error(f"Failed to render **{choice}**")
        st.exception(exc)


if __name__ == "__main__":
    main()
