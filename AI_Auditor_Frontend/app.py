"""
TPI Due Diligence — AI Audit Dashboard
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit as st

from config import (
    COMPANY,
    DEPARTMENT,
    DETAIL_COLUMNS,
    FILTER_OPTIONS,
    OUTPUT_DIR,
    RISK_COLORS,
    ROWS_PER_PAGE,
)
from utils.data_loader import get_summary_stats, list_output_files, load_excel

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Audit Dashboard",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Base */
[data-testid="stAppViewContainer"] { background:#F0F3F8; }

/* Sidebar */
[data-testid="stSidebar"] { background:#1E3A5F !important; min-width:240px; max-width:280px; }
[data-testid="stSidebar"] * { color:#D4E3F5 !important; }
[data-testid="stSidebar"] h3 { color:#FFFFFF !important; font-size:15px; }
[data-testid="stSidebar"] hr { border-color:#2C5282 !important; }
[data-testid="stSidebar"] .stTextInput input {
    background:#2C5282; color:white; border:1px solid #3A6FAD;
    border-radius:4px; font-size:11px;
}

/* Dialog — let Streamlit's native white background and overlay do the work */
section[data-testid="stSidebar"] + div [data-testid="stDialog"] {
    backdrop-filter: blur(4px);
}

/* Top header */
.top-header {
    background:linear-gradient(90deg,#1B2A40 0%,#2C3E5A 100%);
    border-radius:8px 8px 0 0;
    padding:0; display:flex; align-items:stretch; margin-bottom:0;
}
.header-brand {
    background:#C00000; color:white; font-weight:800; font-size:20px;
    padding:16px 28px; border-radius:8px 0 0 0; letter-spacing:0.2px;
    display:flex; align-items:center;
}
.header-meta {
    flex:1; color:#A8C5E8; font-size:12px;
    display:flex; align-items:center; padding:0 28px; gap:6px;
}
.header-meta b { color:#FFFFFF; }
.header-file { color:#7EB5E0; font-size:11px; padding:0 24px; display:flex; align-items:center; }

/* Breadcrumb */
.breadcrumb-bar {
    background:#FFFFFF; border-bottom:1px solid #DDE1E9;
    padding:7px 20px; font-size:12px; color:#6B7280;
    display:flex; justify-content:space-between; align-items:center;
}
.status-pill {
    background:#E8F5E9; color:#2E7D32; border:1px solid #A5D6A7;
    padding:2px 12px; border-radius:20px; font-size:11px; font-weight:700;
}

/* Action strip */
.action-strip {
    background:#F8F9FB; border-bottom:1px solid #DDE1E9; padding:8px 20px;
    display:flex; justify-content:flex-end; gap:10px;
    margin-bottom:12px; border-radius:0 0 8px 8px;
}
.btn-outline {
    padding:5px 18px; border-radius:4px; font-size:12px; font-weight:600;
    border:1.5px solid #C00000; color:#C00000; background:white; cursor:pointer;
}
.btn-solid {
    padding:5px 18px; border-radius:4px; font-size:12px; font-weight:600;
    border:none; background:#1E3A5F; color:white; cursor:pointer;
}

/* Metrics */
.metric-row { display:flex; gap:12px; margin-bottom:14px; }
.metric-card {
    flex:1; background:white; border-radius:8px; padding:14px 20px;
    box-shadow:0 1px 4px rgba(0,0,0,0.07); border-top:4px solid; text-align:center;
}
.m-label { font-size:10.5px; color:#6B7280; text-transform:uppercase; letter-spacing:0.8px; font-weight:700; }
.m-value { font-size:32px; font-weight:800; margin-top:6px; }
.mc-total { border-color:#1E3A5F; } .mc-total .m-value { color:#1E3A5F; }
.mc-risk  { border-color:#C00000; } .mc-risk  .m-value { color:#C00000; }
.mc-frr   { border-color:#E65100; } .mc-frr   .m-value { color:#E65100; }
.mc-nr    { border-color:#2E7D32; } .mc-nr    .m-value { color:#2E7D32; }

/* Finding card */
.finding-card {
    background:white; border-radius:8px; border-left:5px solid;
    box-shadow:0 1px 4px rgba(0,0,0,0.07); margin-bottom:12px; overflow:hidden;
}
.finding-card-head {
    background:#F8F9FB; border-bottom:1px solid #E8EBF0;
    padding:8px 16px; display:flex; align-items:center; gap:10px;
}
.fc-num  { font-size:15px; font-weight:800; color:#6B7280; min-width:28px; }
.fc-scope { font-size:12px; font-weight:600; color:#374151; flex:1; }

/* Table col headers */
.tbl-col-header {
    font-size:10px; font-weight:800; text-transform:uppercase;
    letter-spacing:0.7px; color:#9CA3AF; margin-bottom:5px;
}

/* Fixed-height cells */
.tbl-cell {
    background:#FAFBFC; border:1px solid #E8EBF0; border-radius:5px;
    padding:10px 12px; font-size:12.5px; color:#1F2937; line-height:1.6;
    height:110px; overflow:hidden; word-break:break-word;
    position:relative;
}
.tbl-cell::after {
    content:""; position:absolute; bottom:0; left:0; right:0;
    height:32px;
    background:linear-gradient(transparent, #FAFBFC);
    pointer-events:none;
}
.tbl-cell-score {
    background:#F0F4FA; border:1px solid #D1DCF0; border-radius:5px;
    padding:10px 12px; font-size:26px; font-weight:900;
    text-align:center; height:110px;
    display:flex; align-items:center; justify-content:center;
}

/* Risk badge */
.risk-badge {
    display:inline-block; padding:3px 12px; border-radius:12px;
    font-size:11px; font-weight:700; color:white; white-space:normal;
    text-align:center; line-height:1.4;
}
.tbl-cell-badge {
    background:#FAFBFC; border:1px solid #E8EBF0; border-radius:5px;
    padding:10px 12px; height:110px;
    display:flex; align-items:center; justify-content:center;
}

/* Read-more button */
div[data-testid="stButton"] > button[kind="secondary"] {
    font-size:11px; padding:2px 8px; border-radius:4px;
    color:#1E3A5F; border:1px solid #C5D3E8; background:#EEF3FB;
    font-weight:600; margin-top:4px;
}

/* View Details button */
.view-btn > div[data-testid="stButton"] > button {
    background:#1E3A5F !important; color:white !important;
    font-weight:700 !important; font-size:12px !important;
    border:none !important; border-radius:5px !important;
    width:100% !important;
}

/* Dialog content cards */
.dlg-section-title {
    font-size:11px; font-weight:800; text-transform:uppercase;
    letter-spacing:0.8px; color:#6B7280; margin:14px 0 6px 0;
    border-bottom:1px solid #E8EBF0; padding-bottom:4px;
}
.dlg-card {
    background:#F8F9FB; border:1px solid #E0E4EA; border-radius:7px;
    padding:12px 15px; margin-bottom:10px;
}
.dlg-card-label {
    font-size:10px; font-weight:800; text-transform:uppercase;
    letter-spacing:0.8px; color:#9CA3AF; margin-bottom:5px;
}
.dlg-card-value {
    font-size:13px; color:#1F2937; line-height:1.65;
    white-space:pre-wrap; word-break:break-word;
}
.dlg-score {
    font-size:42px; font-weight:900; text-align:center; padding:6px 0;
}

/* Pagination */
.page-info { text-align:center; color:#6B7280; font-size:13px; padding-top:8px; }

/* Footer */
.app-footer {
    background:#1B2A40; color:#7EB5E0; padding:10px 24px;
    border-radius:8px; margin-top:20px; font-size:11.5px;
    display:flex; justify-content:space-between;
}

/* No results */
.no-results {
    text-align:center; padding:60px; color:#9CA3AF;
    background:white; border-radius:8px; border:1px solid #DDE1E9; font-size:15px;
}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_empty(val) -> bool:
    return val is None or str(val).strip() in ("", "nan", "None", "N/A", "Not Available", "—")

def _badge_color(risk_title: str) -> str:
    return RISK_COLORS.get(risk_title, RISK_COLORS["No Risk Identified"])["badge_bg"]

def _border_color(risk_title: str) -> str:
    return RISK_COLORS.get(risk_title, RISK_COLORS["No Risk Identified"])["border"]

def _score_color(score_str: str) -> str:
    try:
        s = float(score_str)
        if s >= 7:  return "#C00000"
        if s >= 4:  return "#E65100"
        return "#2E7D32"
    except Exception:
        return "#1E3A5F"

def _fmt_score(score_str: str) -> str:
    try:
        return f"{float(score_str):.1f}"
    except Exception:
        return score_str or "—"


# ── Dialogs ───────────────────────────────────────────────────────────────────

@st.dialog("Full Content", width="large")
def content_modal() -> None:
    """Read-more modal: shows one field's full text."""
    data    = st.session_state.get("_modal_data", {})
    title   = data.get("title", "")
    content = data.get("content", "")

    st.markdown(
        f"<p style='font-size:11px;font-weight:800;text-transform:uppercase;"
        f"letter-spacing:0.8px;color:#6B7280;margin-bottom:4px;'>{title}</p>",
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown(content)


@st.dialog("Risk Finding — Full Details", width="large")
def detail_modal() -> None:
    """View Details modal: all non-empty fields, native Streamlit components."""
    row        = st.session_state.get("_detail_row")
    risk_title = st.session_state.get("_detail_risk_title", "")
    if row is None:
        st.warning("No data.")
        return

    badge_bg  = _badge_color(risk_title)
    score_raw = str(row.get("Risk Score", "")).strip()
    score_clr = _score_color(score_raw)

    # ── Risk title badge ──────────────────────────────────────────────────────
    st.markdown(
        f'<span style="background:{badge_bg};color:white;padding:5px 18px;'
        f'border-radius:20px;font-size:13px;font-weight:700;">{risk_title}</span>',
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Score + meta row ──────────────────────────────────────────────────────
    col_score, col_meta = st.columns([1, 3])
    with col_score:
        if not _is_empty(score_raw):
            st.markdown(
                f'<div style="background:#F8F9FB;border:1px solid #E0E4EA;border-radius:8px;'
                f'padding:16px;text-align:center;">'
                f'<p style="font-size:10px;font-weight:800;text-transform:uppercase;'
                f'letter-spacing:0.8px;color:#9CA3AF;margin:0 0 6px 0;">Risk Score</p>'
                f'<p style="font-size:42px;font-weight:900;color:{score_clr};margin:0;line-height:1;">'
                f'{_fmt_score(score_raw)}</p>'
                f'<p style="font-size:13px;color:#9CA3AF;margin:4px 0 0 0;">/ 10</p>'
                f'</div>',
                unsafe_allow_html=True,
            )
    with col_meta:
        with st.container(border=True):
            for label, col_name in [("Scope Area",              "Scope Area"),
                                     ("Evidence Classification", "Evidence Classification"),
                                     ("Processed At",            "Processed At")]:
                val = str(row.get(col_name, "")).strip()
                if not _is_empty(val):
                    st.markdown(
                        f"<p style='font-size:10px;font-weight:800;text-transform:uppercase;"
                        f"letter-spacing:0.8px;color:#9CA3AF;margin:0 0 2px 0;'>{label}</p>"
                        f"<p style='font-size:13px;color:#1F2937;margin:0 0 10px 0;'>{val}</p>",
                        unsafe_allow_html=True,
                    )

    st.divider()

    # ── Long-text fields — each in its own bordered container ─────────────────
    long_fields = [
        ("Risk Type",            "Risk Type"),
        ("Risk Description",     "Risk Description"),
        ("Risk Rationale",       "Risk Rationale"),
        ("Risk Score Reasoning", "Risk Score Reasoning"),
        ("Question",             "Question"),
        ("Response",             "Response"),
        ("Follow-up Question",   "Follow-up Question"),
        ("Follow-up Response",   "Follow-up Response"),
        ("Calculation Evidence", "Calculation Evidence"),
        ("Source Details",       "Source Details"),
        ("Evidence Explanation", "Evidence Explanation"),
    ]
    for label, col_name in long_fields:
        val = str(row.get(col_name, "")).strip()
        if _is_empty(val):
            continue
        with st.container(border=True):
            st.markdown(
                f"<p style='font-size:10px;font-weight:800;text-transform:uppercase;"
                f"letter-spacing:0.8px;color:#9CA3AF;margin:0 0 5px 0;'>{label}</p>",
                unsafe_allow_html=True,
            )
            st.markdown(val)


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar() -> Path | None:
    with st.sidebar:
        st.markdown("### 🔍 AI Audit Dashboard")
        st.markdown("<div style='font-size:11px;color:#7EB5E0;margin-top:-8px;'>Johnson & Johnson</div>",
                    unsafe_allow_html=True)
        st.divider()

        st.markdown("**Output Directory**")
        raw_dir = st.text_input("dir", value=str(OUTPUT_DIR),
                                label_visibility="collapsed", key="output_dir")
        out_dir = Path(raw_dir.strip())
        if not out_dir.exists():
            st.warning("Directory not found.")

        st.divider()
        st.markdown("**Select Report**")
        files = list_output_files(out_dir)
        if not files:
            st.info("No output files found.")
            return None

        file_names = [f.name for f in files]
        idx = st.radio("files", range(len(file_names)),
                       format_func=lambda i: file_names[i],
                       label_visibility="collapsed", key="file_radio")
        selected = files[idx]

        st.divider()
        st.markdown("<div style='font-size:10px;color:#4A7FA8;text-align:center;'>"
                    "TPI Due Diligence Pipeline</div>", unsafe_allow_html=True)
    return selected


# ── Header ────────────────────────────────────────────────────────────────────

def render_header(file_name: str | None, total: int | None = None) -> None:
    count = f"({total})" if total is not None else ""
    fname = file_name or "No file selected"
    st.markdown(f"""
    <div class="top-header">
        <div class="header-brand">Risk Analysis {count}</div>
        <div class="header-meta">
            Phase: <b>TPI Due Diligence</b> &nbsp;|&nbsp;
            Task: <b>Risk Analysis</b> &nbsp;|&nbsp;
            Status: <b>Output Generated</b>
        </div>
        <div class="header-file">{fname}</div>
    </div>
    <div class="breadcrumb-bar">
        <span>Home &gt; TPI Audits &gt; Risk Analysis</span>
        <span class="status-pill">✓ Output Generated</span>
    </div>
    <div class="action-strip">
        <button class="btn-outline">⬇ Download</button>
        <button class="btn-solid">Activity Log</button>
    </div>
    """, unsafe_allow_html=True)


# ── Metrics ───────────────────────────────────────────────────────────────────

def render_metrics(stats: dict[str, int]) -> None:
    st.markdown(f"""
    <div class="metric-row">
        <div class="metric-card mc-total">
            <div class="m-label">Total Findings</div>
            <div class="m-value">{stats["Total"]}</div>
        </div>
        <div class="metric-card mc-risk">
            <div class="m-label">Potential Risk</div>
            <div class="m-value">{stats["Potential Risk"]}</div>
        </div>
        <div class="metric-card mc-frr">
            <div class="m-label">Further Review</div>
            <div class="m-value">{stats["Further Review Required"]}</div>
        </div>
        <div class="metric-card mc-nr">
            <div class="m-label">No Risk</div>
            <div class="m-value">{stats["No Risk Identified"]}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Filter row ────────────────────────────────────────────────────────────────

def render_filter_row() -> list[str]:
    st.markdown('<div style="font-size:12px;font-weight:700;color:#374151;margin-bottom:4px;">Filter by Risk Type</div>',
                unsafe_allow_html=True)
    cols = st.columns(len(FILTER_OPTIONS) + 1)
    active: list[str] = []
    for i, opt in enumerate(FILTER_OPTIONS):
        cfg = RISK_COLORS[opt]
        with cols[i]:
            if st.checkbox(f"{cfg['icon']}  {opt}", value=True, key=f"filter_{opt}"):
                active.append(opt)
    return active


# ── Single finding row ────────────────────────────────────────────────────────

def render_finding_row(row: pd.Series, display_num: int) -> None:
    risk_title = str(row.get("Risk Title", "")).strip() or "No Risk Identified"
    risk_type  = str(row.get("Risk Type", "")).strip()  or "—"
    risk_desc  = str(row.get("Risk Description", "")).strip()
    risk_rat   = str(row.get("Risk Rationale", "")).strip()
    score_raw  = str(row.get("Risk Score", "")).strip()
    scope_area = str(row.get("Scope Area", "")).strip()

    border_clr = _border_color(risk_title)
    badge_bg   = _badge_color(risk_title)
    score_clr  = _score_color(score_raw)
    score_disp = _fmt_score(score_raw)

    # Card header
    st.markdown(f"""
    <div class="finding-card" style="border-left-color:{border_clr};">
        <div class="finding-card-head">
            <span class="fc-num">#{display_num}</span>
            <span class="fc-scope">{scope_area or "TPI Due Diligence"}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Column headers ────────────────────────────────────────────────────────
    h0, h1, h2, h3, h4, h5 = st.columns([1.8, 2.0, 4.0, 4.0, 1.2, 1.5])
    with h0: st.markdown('<div class="tbl-col-header">Risk Title</div>', unsafe_allow_html=True)
    with h1: st.markdown('<div class="tbl-col-header">Risk Type</div>', unsafe_allow_html=True)
    with h2: st.markdown('<div class="tbl-col-header">Risk Description</div>', unsafe_allow_html=True)
    with h3: st.markdown('<div class="tbl-col-header">Risk Rationale</div>', unsafe_allow_html=True)
    with h4: st.markdown('<div class="tbl-col-header">Score</div>', unsafe_allow_html=True)
    with h5: st.markdown('<div class="tbl-col-header">&nbsp;</div>', unsafe_allow_html=True)

    # ── Data cells ────────────────────────────────────────────────────────────
    c0, c1, c2, c3, c4, c5 = st.columns([1.8, 2.0, 4.0, 4.0, 1.2, 1.5])

    # Risk Title — badge only
    with c0:
        st.markdown(
            f'<div class="tbl-cell-badge">'
            f'<span class="risk-badge" style="background:{badge_bg};">{risk_title}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Risk Type — fixed height + Read more
    with c1:
        st.markdown(f'<div class="tbl-cell">{risk_type}</div>', unsafe_allow_html=True)
        if len(risk_type) > 120:
            if st.button("↗ Read more", key=f"rm_type_{display_num}"):
                st.session_state["_modal_data"] = {"title": "Risk Type", "content": risk_type}
                content_modal()

    # Risk Description — fixed height + Read more
    with c2:
        st.markdown(f'<div class="tbl-cell">{risk_desc}</div>', unsafe_allow_html=True)
        if len(risk_desc) > 180:
            if st.button("↗ Read more", key=f"rm_desc_{display_num}"):
                st.session_state["_modal_data"] = {"title": "Risk Description", "content": risk_desc}
                content_modal()

    # Risk Rationale — fixed height + Read more
    with c3:
        st.markdown(f'<div class="tbl-cell">{risk_rat}</div>', unsafe_allow_html=True)
        if len(risk_rat) > 180:
            if st.button("↗ Read more", key=f"rm_rat_{display_num}"):
                st.session_state["_modal_data"] = {"title": "Risk Rationale", "content": risk_rat}
                content_modal()

    # Score
    with c4:
        st.markdown(
            f'<div class="tbl-cell-score" style="color:{score_clr};">{score_disp}</div>',
            unsafe_allow_html=True,
        )

    # View Details — opens full detail modal
    with c5:
        st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)
        st.markdown('<div class="view-btn">', unsafe_allow_html=True)
        if st.button("View Details", key=f"det_{display_num}", use_container_width=True):
            st.session_state["_detail_row"]        = row
            st.session_state["_detail_risk_title"] = risk_title
            detail_modal()
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<hr style='border:none;border-top:1px solid #E8EBF0;margin:6px 0 0 0;'>",
                unsafe_allow_html=True)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    selected_file = render_sidebar()

    if selected_file is None:
        render_header(None)
        st.markdown('<div class="no-results">Select a report file from the sidebar to begin.</div>',
                    unsafe_allow_html=True)
        render_footer()
        return

    with st.spinner(f"Loading {selected_file.name}…"):
        try:
            df = load_excel(selected_file)
        except ValueError as exc:
            st.error(str(exc))
            return

    if df.empty:
        render_header(selected_file.name)
        st.warning("No data rows in this file.")
        render_footer()
        return

    stats = get_summary_stats(df)
    render_header(selected_file.name, stats["Total"])
    render_metrics(stats)

    active_filters = render_filter_row()

    search_term = st.text_input(
        "Search",
        placeholder="Search questions, descriptions, scope area, risk type…",
        label_visibility="collapsed",
        key="search",
    )

    # Filter
    title_col = "Risk Title" if "Risk Title" in df.columns else df.columns[1]
    if active_filters:
        mask = df[title_col].apply(lambda v: any(f.lower() in str(v).lower() for f in active_filters))
        filtered = df[mask].reset_index(drop=True)
    else:
        filtered = pd.DataFrame(columns=df.columns)

    if search_term.strip():
        term = search_term.strip().lower()
        s_cols = [c for c in ["Question", "Risk Description", "Risk Type", "Scope Area", "Risk Rationale"]
                  if c in filtered.columns]
        if s_cols:
            hit = filtered[s_cols].apply(lambda col: col.str.lower().str.contains(term, na=False)).any(axis=1)
            filtered = filtered[hit].reset_index(drop=True)

    if filtered.empty:
        st.markdown('<div class="no-results">No findings match the current filters.</div>',
                    unsafe_allow_html=True)
        render_footer()
        return

    # Reset page on filter change
    fkey = (tuple(sorted(active_filters)), search_term, selected_file.name)
    if st.session_state.get("_last_filter") != fkey:
        st.session_state["page"] = 0
        st.session_state["_last_filter"] = fkey
    if "page" not in st.session_state:
        st.session_state["page"] = 0

    total_pages = max(1, math.ceil(len(filtered) / ROWS_PER_PAGE))
    page  = st.session_state["page"]
    start = page * ROWS_PER_PAGE
    end   = min(start + ROWS_PER_PAGE, len(filtered))

    st.markdown(
        f'<div style="font-size:12px;color:#6B7280;margin:8px 0;">'
        f'Showing {start+1}–{end} of {len(filtered)} findings</div>',
        unsafe_allow_html=True,
    )

    for idx, (_, row) in enumerate(filtered.iloc[start:end].iterrows()):
        render_finding_row(row, start + idx + 1)

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
    col_prev, col_mid, col_next = st.columns([1, 3, 1])
    with col_prev:
        if st.button("◀  Previous", disabled=(page == 0), use_container_width=True):
            st.session_state["page"] = page - 1
            st.rerun()
    with col_mid:
        st.markdown(f'<div class="page-info">Page {page+1} of {total_pages}</div>',
                    unsafe_allow_html=True)
    with col_next:
        if st.button("Next  ▶", disabled=(page >= total_pages - 1), use_container_width=True):
            st.session_state["page"] = page + 1
            st.rerun()

    render_footer()


def render_footer() -> None:
    st.markdown(f"""
    <div class="app-footer">
        <span>{COMPANY}</span>
        <span>TPI Due Diligence — AI Audit Dashboard</span>
        <span>{DEPARTMENT}</span>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
