"""Semantic comparison of an output table vs its chat-edited `_copy`.

Rows are aligned by a key column (default: the first column). For each common key the two rows are
flattened to text and embedded with the MAIN app's Azure embedding model; cosine similarity scores how
much the chat edits changed the row. Rows present on only one side are reported as added/removed. This
surfaces WHAT reviewers changed through the chat flow, and HOW MUCH (semantically), not just byte diffs.
"""

from __future__ import annotations

import difflib

import numpy as np
import pandas as pd
import streamlit as st

from config import get_settings

# Rows with cosine similarity at/above this are treated as semantically unchanged (minor wording only).
DEFAULT_SIM_THRESHOLD = 0.98


@st.cache_resource(show_spinner=False)
def _embedder():
    from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider

    return AzureOpenAIEmbeddingProvider(get_settings()).get_client()


def _row_text(row: pd.Series, key_col: str) -> str:
    """Flatten a row (excluding the key) to 'col: value | col: value' text for embedding/diffing."""
    return " | ".join(
        f"{c}: {('' if pd.isna(v) else v)}" for c, v in row.items() if c != key_col
    )


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def compare(
    df_orig: pd.DataFrame,
    df_copy: pd.DataFrame,
    key_col: str,
    threshold: float = DEFAULT_SIM_THRESHOLD,
    max_rows: int = 500,
) -> dict:
    """Return {summary, table, texts} describing the semantic delta between original and copy.

    `table` is a per-key DataFrame (status, similarity, exact_match). `texts` maps key → (orig, copy)
    for the modified rows so the UI can render a readable field-level diff.
    """
    orig = {str(r[key_col]): r for _, r in df_orig.iterrows()}
    copy = {str(r[key_col]): r for _, r in df_copy.iterrows()}
    orig_keys, copy_keys = set(orig), set(copy)

    common = sorted(orig_keys & copy_keys)
    added = sorted(copy_keys - orig_keys)
    removed = sorted(orig_keys - copy_keys)

    # Only embed rows whose text actually differs (exact match ⇒ similarity 1.0, no API cost).
    texts: dict[str, tuple[str, str]] = {}
    exact_match: dict[str, bool] = {}
    to_embed: list[str] = []
    for k in common:
        ot, ct = _row_text(orig[k], key_col), _row_text(copy[k], key_col)
        texts[k] = (ot, ct)
        same = ot == ct
        exact_match[k] = same
        if not same:
            to_embed.append(k)

    truncated = len(to_embed) > max_rows
    to_embed = to_embed[:max_rows]

    sims: dict[str, float] = {k: 1.0 for k in common if exact_match[k]}
    if to_embed:
        emb = _embedder()
        o_vecs = emb.embed_documents([texts[k][0] for k in to_embed])
        c_vecs = emb.embed_documents([texts[k][1] for k in to_embed])
        for k, ov, cv in zip(to_embed, o_vecs, c_vecs):
            sims[k] = _cosine(np.asarray(ov, dtype=float), np.asarray(cv, dtype=float))

    rows = []
    for k in common:
        sim = sims.get(k, 1.0)
        status = "identical" if exact_match[k] else ("minor" if sim >= threshold else "modified")
        rows.append({"key": k, "status": status, "similarity": round(sim, 4),
                     "exact_match": exact_match[k]})
    for k in added:
        rows.append({"key": k, "status": "added", "similarity": 0.0, "exact_match": False})
    for k in removed:
        rows.append({"key": k, "status": "removed", "similarity": 0.0, "exact_match": False})

    table = pd.DataFrame(rows)
    summary = {
        "common": len(common),
        "added": len(added),
        "removed": len(removed),
        "modified": int((table["status"] == "modified").sum()) if not table.empty else 0,
        "minor": int((table["status"] == "minor").sum()) if not table.empty else 0,
        "identical": int((table["status"] == "identical").sum()) if not table.empty else 0,
        "mean_similarity": round(float(np.mean([sims[k] for k in common])), 4) if common else None,
        "truncated": truncated,
        "max_rows": max_rows,
    }
    return {"summary": summary, "table": table, "texts": texts}


def field_diff(orig_row: pd.Series, copy_row: pd.Series, key_col: str) -> pd.DataFrame:
    """Per-column before/after for one modified row — only the columns that actually changed."""
    rows = []
    for col in orig_row.index:
        if col == key_col:
            continue
        o = "" if pd.isna(orig_row.get(col)) else str(orig_row.get(col))
        c = "" if pd.isna(copy_row.get(col)) else str(copy_row.get(col))
        if o != c:
            rows.append({"column": col, "original": o, "edited (_copy)": c})
    return pd.DataFrame(rows)


def inline_diff(a: str, b: str) -> str:
    """A compact unified text diff (for an expander), so reviewers see the exact chat edit."""
    diff = difflib.unified_diff(a.splitlines(), b.splitlines(), "original", "copy", lineterm="")
    return "\n".join(diff) or "(no line-level differences)"
