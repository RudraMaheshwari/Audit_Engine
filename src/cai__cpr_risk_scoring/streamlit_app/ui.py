"""Reusable UI building blocks — KPI cards, section headers, top nav, empty/skeleton states, and a
fragment decorator (so a view's interactions rerun only that view, not the whole app → snappier)."""

from __future__ import annotations

import streamlit as st

import theme

# Fragment decorator: st.fragment (1.37+) → experimental_fragment → no-op, so it runs on any version.
try:
    fragment = st.fragment
except AttributeError:
    fragment = getattr(st, "experimental_fragment", None) or (lambda f=None, **k: (f or (lambda g: g)))


def themed(fig, height: int = 320):
    """Apply the active theme to a Plotly figure and return it."""
    fig.update_layout(**theme.plotly_layout(height))
    return fig


def top_nav(options: list[str], key: str = "nav") -> str:
    """Horizontal nav bar. Uses st.segmented_control when available, else a horizontal radio."""
    default = st.session_state.get(key, options[0])
    sc = getattr(st, "segmented_control", None)
    if sc is not None:
        try:
            choice = sc("nav", options, default=default, label_visibility="collapsed", key=f"{key}_sc")
        except TypeError:  # older signature
            choice = sc("nav", options, label_visibility="collapsed", key=f"{key}_sc")
        choice = choice or default
    else:
        choice = st.radio("nav", options, horizontal=True, label_visibility="collapsed", key=f"{key}_rd")
    st.session_state[key] = choice
    return choice


def kpi_row(items: list[tuple]) -> None:
    """items: list of (label, value) or (label, value, sub). Renders styled KPI cards in one row."""
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        label, value = item[0], item[1]
        sub = item[2] if len(item) > 2 else ""
        col.markdown(
            f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-sub">{sub}</div></div>',
            unsafe_allow_html=True,
        )


def section(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="section"><h2>{title}</h2><p>{subtitle}</p></div>', unsafe_allow_html=True)


def panel_title(text: str) -> None:
    st.markdown(f'<div style="font-weight:700;margin:.2rem 0 .1rem">{text}</div>', unsafe_allow_html=True)


def empty_state(icon: str, title: str, message: str) -> None:
    st.markdown(
        f'<div class="empty"><div class="empty-ico">{icon}</div>'
        f'<div class="empty-title">{title}</div><div class="empty-msg">{message}</div></div>',
        unsafe_allow_html=True,
    )


def skeleton(cols: int = 4) -> None:
    for c in st.columns(cols):
        c.markdown('<div class="skel"></div>', unsafe_allow_html=True)
