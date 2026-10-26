"""Theme engine — dark/light palettes, global CSS, and a matching Plotly layout.

A single session-state flag (`theme`) drives everything: CSS variables for the whole app and a Plotly
layout dict the charts reuse, so dark/light switching is instant and consistent.
"""

from __future__ import annotations

from string import Template

import streamlit as st

_PALETTES: dict[str, dict] = {
    "dark": {
        "bg": "#0b1020", "bg2": "#0e1430", "card": "#141b33", "card2": "#1a2344",
        "text": "#e8ecf8", "muted": "#9aa6c2", "border": "#243055",
        "accent": "#ff4d4f", "accent2": "#5b8cff", "shadow": "rgba(0,0,0,0.45)",
        "grid": "rgba(255,255,255,0.07)", "hover": "#1d2850",
    },
    "light": {
        "bg": "#f4f6fc", "bg2": "#eaeef8", "card": "#ffffff", "card2": "#f7f9ff",
        "text": "#141a2e", "muted": "#5c6680", "border": "#e3e8f4",
        "accent": "#eb1700", "accent2": "#2f6bff", "shadow": "rgba(20,26,46,0.08)",
        "grid": "rgba(20,26,46,0.08)", "hover": "#eef3ff",
    },
}

# Categorical colorway shared by all charts (works on both themes).
COLORWAY = ["#5b8cff", "#ff7a59", "#28c76f", "#ff4d4f", "#a78bfa", "#ffc94d", "#22d3ee", "#f472b6"]


def current() -> str:
    return st.session_state.get("theme", "dark")


def toggle() -> None:
    st.session_state["theme"] = "light" if current() == "dark" else "dark"


def palette() -> dict:
    return _PALETTES[current()]


def plotly_layout(height: int = 320) -> dict:
    """Layout dict applied to every figure so charts match the active theme (transparent bg)."""
    p = palette()
    axis = dict(gridcolor=p["grid"], zerolinecolor=p["grid"], linecolor=p["border"],
                tickfont=dict(color=p["muted"]), title_font=dict(color=p["muted"]))
    return dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p["text"], family="Inter, 'Segoe UI', system-ui, sans-serif"),
        colorway=COLORWAY, xaxis=axis, yaxis=axis,
        margin=dict(l=4, r=4, t=10, b=4), height=height,
        legend=dict(font=dict(color=p["muted"])),
    )


_CSS = Template("""
:root { --bg:$bg; --bg2:$bg2; --card:$card; --card2:$card2; --text:$text; --muted:$muted;
        --border:$border; --accent:$accent; --accent2:$accent2; --grid:$grid; --hover:$hover; --shadow:$shadow; }

.stApp { background: radial-gradient(1200px 600px at 10% -10%, var(--bg2), var(--bg)) fixed; color: var(--text); }
[data-testid="stSidebar"], #MainMenu, footer, [data-testid="stStatusWidget"] { display:none !important; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1400px; }
body, .stMarkdown, p, span, label, div { font-family: Inter, 'Segoe UI', system-ui, sans-serif; }

/* ── Top bar ────────────────────────────────────────────── */
.topbar { display:flex; align-items:center; justify-content:space-between; gap:1rem;
          padding:.65rem 1.1rem; margin-bottom:.4rem; border:1px solid var(--border);
          border-radius:16px; background:linear-gradient(180deg, var(--card), var(--card2));
          box-shadow:0 6px 24px var(--shadow); }
.brand { display:flex; align-items:center; gap:.6rem; font-weight:800; font-size:1.05rem; letter-spacing:.2px; }
.brand .dot { width:10px; height:10px; border-radius:50%; background:var(--accent);
              box-shadow:0 0 14px var(--accent); }
.brand small { color:var(--muted); font-weight:600; }

/* segmented-control → nav pills */
[data-testid="stSegmentedControl"] { background:transparent; }
[data-testid="stSegmentedControl"] button { border-radius:12px !important; color:var(--muted) !important;
    border:1px solid transparent !important; font-weight:600 !important; }
[data-testid="stSegmentedControl"] button[aria-checked="true"],
[data-testid="stSegmentedControl"] button[aria-selected="true"] {
    background:var(--accent) !important; color:#fff !important; box-shadow:0 4px 14px var(--shadow) !important; }

/* ── KPI cards ──────────────────────────────────────────── */
.kpi-card { border:1px solid var(--border); border-radius:16px; padding:.9rem 1.05rem;
    background:linear-gradient(180deg, var(--card), var(--card2)); box-shadow:0 6px 20px var(--shadow);
    transition:transform .15s ease, box-shadow .15s ease; height:100%; }
.kpi-card:hover { transform:translateY(-3px); box-shadow:0 12px 28px var(--shadow); }
.kpi-label { color:var(--muted); font-size:.72rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
.kpi-value { font-size:1.7rem; font-weight:800; margin-top:.15rem; line-height:1.1; }
.kpi-sub { color:var(--muted); font-size:.78rem; margin-top:.2rem; }

/* ── Section header ─────────────────────────────────────── */
.section { margin:.4rem 0 .6rem; }
.section h2 { margin:0; font-size:1.25rem; font-weight:800; }
.section p { margin:.15rem 0 0; color:var(--muted); font-size:.85rem; }

/* ── Panels / charts / tables ───────────────────────────── */
[data-testid="stPlotlyChart"], [data-testid="stDataFrame"], [data-testid="stTable"] {
    border:1px solid var(--border); border-radius:16px; padding:.5rem; background:var(--card);
    box-shadow:0 6px 20px var(--shadow); }
[data-testid="stExpander"] { border:1px solid var(--border) !important; border-radius:14px !important;
    background:var(--card) !important; }

/* ── Empty state ────────────────────────────────────────── */
.empty { text-align:center; padding:3rem 1rem; border:1px dashed var(--border); border-radius:18px;
    background:var(--card); color:var(--muted); }
.empty-ico { font-size:2.4rem; }
.empty-title { font-size:1.05rem; font-weight:700; color:var(--text); margin-top:.3rem; }
.empty-msg { font-size:.88rem; margin-top:.25rem; }

/* skeleton shimmer */
.skel { height:96px; border-radius:16px; border:1px solid var(--border);
    background:linear-gradient(100deg, var(--card) 30%, var(--hover) 50%, var(--card) 70%);
    background-size:200% 100%; animation:sh 1.2s infinite linear; }
@keyframes sh { to { background-position:-200% 0; } }

/* buttons */
.stButton button { border-radius:12px; border:1px solid var(--border); background:var(--card);
    color:var(--text); font-weight:600; }
.stButton button:hover { border-color:var(--accent); color:var(--accent); }

/* bordered containers → cards (top bar, input panels) */
[data-testid="stVerticalBlockBorderWrapper"] { border:1px solid var(--border) !important; border-radius:16px;
    background:linear-gradient(180deg, var(--card), var(--card2)); box-shadow:0 6px 20px var(--shadow);
    padding:.35rem .7rem; }
.stSlider label, .stTextInput label, .stSelectbox label, .stRadio label { color:var(--muted) !important; }
""")


def inject_css() -> None:
    st.markdown(f"<style>{_CSS.substitute(**palette())}</style>", unsafe_allow_html=True)
