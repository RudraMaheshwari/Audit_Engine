"""Semantic comparison of an output table vs its chat-edited `_copy` — for retraining.

Aligns rows by a key, scores each row's change with embedding cosine similarity, extracts the exact
field-level before/after, and (for the modified rows) asks the LLM to SUMMARISE what the reviewer
changed and why it matters — a structured signal you can fold back into training/feedback datasets.

Pure functions over the main app's Databricks SQL engine, embeddings and LLM. Best-effort: similarity
and summaries degrade gracefully if the embedding/LLM provider is unavailable.
"""

from __future__ import annotations

import difflib
import json
import math

from backend.bootstrap import settings

DEFAULT_SIM_THRESHOLD = 0.98
_MAX_EMBED_ROWS = 400
_MAX_SUMMARY_ROWS = 60


# ── Delta reads ─────────────────────────────────────────────────────────────────
def output_schema() -> str:
    return getattr(settings(), "risk_analysis_output_schema", "risk_analysis_output")


def fqn(base: str, engagement: str, copy: bool = False) -> str:
    s = settings()
    name = f"{base}_{engagement}" + ("_copy" if copy else "")
    return f"{s.databricks_catalog}.{output_schema()}.{name}"


def read_table(table_fqn: str, limit: int = 5000) -> tuple[list[str], list[dict]]:
    from sqlalchemy import text

    from src.config.databricks_auth import build_engine

    engine = build_engine(settings(), pool_pre_ping=True)
    with engine.connect() as c:
        res = c.execute(text(f"SELECT * FROM {table_fqn} LIMIT {int(limit)}"))
        cols = list(res.keys())
        rows = [dict(zip(cols, r)) for r in res.fetchall()]
    return cols, rows


# ── Similarity helpers ──────────────────────────────────────────────────────────
def _row_text(row: dict, key_col: str) -> str:
    return " | ".join(f"{c}: {'' if row.get(c) is None else row.get(c)}" for c in row if c != key_col)


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return float(dot / (na * nb)) if na and nb else 0.0


def _field_diffs(o: dict, c: dict, key_col: str) -> list[dict]:
    out = []
    for col in o:
        if col == key_col:
            continue
        ov = "" if o.get(col) is None else str(o.get(col))
        cv = "" if c.get(col) is None else str(c.get(col))
        if ov != cv:
            out.append({"column": col, "before": ov, "after": cv})
    return out


def _inline_diff(a: str, b: str) -> str:
    return "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), "before", "after", lineterm="")) \
        or "(no line-level differences)"


# ── Public entry point ──────────────────────────────────────────────────────────
def compare(orig_fqn: str, copy_fqn: str, key_col: str | None = None,
            threshold: float = DEFAULT_SIM_THRESHOLD, summarize: bool = True) -> dict:
    o_cols, o_rows = read_table(orig_fqn)
    c_cols, c_rows = read_table(copy_fqn)
    if not o_rows or not c_rows:
        return {"error": "one of the tables is empty", "columns": o_cols or c_cols}

    key = key_col if key_col in o_cols else o_cols[0]
    o_by = {str(r.get(key)): r for r in o_rows}
    c_by = {str(r.get(key)): r for r in c_rows}
    common = sorted(set(o_by) & set(c_by))
    added = sorted(set(c_by) - set(o_by))
    removed = sorted(set(o_by) - set(c_by))

    # Exact-diff first; embed only rows that actually changed (no API cost for identical rows).
    changed_keys, texts, exact = [], {}, {}
    for k in common:
        ot, ct = _row_text(o_by[k], key), _row_text(c_by[k], key)
        texts[k] = (ot, ct)
        exact[k] = ot == ct
        if not exact[k]:
            changed_keys.append(k)

    sims = {k: 1.0 for k in common if exact[k]}
    to_embed = changed_keys[:_MAX_EMBED_ROWS]
    if to_embed:
        try:
            emb = _embedder()
            ov = emb.embed_documents([texts[k][0] for k in to_embed])
            cv = emb.embed_documents([texts[k][1] for k in to_embed])
            for k, a, b in zip(to_embed, ov, cv):
                sims[k] = round(_cosine(a, b), 4)
        except Exception:  # noqa: BLE001 — degrade: treat changed rows as modified without a score
            for k in to_embed:
                sims[k] = None

    rows, modified = [], []
    for k in common:
        sim = sims.get(k)
        if exact[k]:
            status = "identical"
        elif sim is None or sim < threshold:
            status = "modified"
        else:
            status = "minor"
        row = {"key": k, "status": status, "similarity": sim,
               "diffs": _field_diffs(o_by[k], c_by[k], key) if status in ("modified", "minor") else []}
        if status == "modified":
            row["inline_diff"] = _inline_diff(*texts[k])
            modified.append(row)
        rows.append(row)
    for k in added:
        rows.append({"key": k, "status": "added", "similarity": None, "diffs": []})
    for k in removed:
        rows.append({"key": k, "status": "removed", "similarity": None, "diffs": []})

    # LLM summaries of the edits — the retraining signal.
    if summarize and modified:
        _attach_summaries(modified)

    scored = [s for s in (sims.get(k) for k in common) if isinstance(s, (int, float))]
    summary = {
        "key_column": key, "columns": o_cols,
        "common": len(common), "added": len(added), "removed": len(removed),
        "modified": sum(1 for r in rows if r["status"] == "modified"),
        "minor": sum(1 for r in rows if r["status"] == "minor"),
        "identical": sum(1 for r in rows if r["status"] == "identical"),
        "mean_similarity": round(sum(scored) / len(scored), 4) if scored else None,
        "embedded": len(to_embed), "truncated": len(changed_keys) > _MAX_EMBED_ROWS,
    }
    return {"summary": summary, "rows": rows}


def _embedder():
    from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider

    return AzureOpenAIEmbeddingProvider(settings()).get_client()


_SUMMARY_SYSTEM = (
    "You analyse reviewer edits to an AI audit output so the system can be retrained from them. For each "
    "edited row you are given the field-level before/after. Return ONLY a JSON array, one object per row "
    "in the SAME order: {\"key\": <row key>, \"change_type\": <short tag>, \"summary\": <1-2 sentence "
    "plain-English description of WHAT the reviewer changed and WHY it matters for training>}. "
    "change_type is one of: wording/hedging, evidence correction, scope correction, severity change, "
    "added detail, removed detail, factual fix, other. Be specific and faithful — do not invent."
)


def _attach_summaries(modified: list[dict]) -> None:
    """Best-effort: one LLM call summarising every modified row; attaches 'change_type' + 'summary'."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider

        subset = modified[:_MAX_SUMMARY_ROWS]
        payload = [{"key": r["key"], "diffs": r["diffs"]} for r in subset]
        llm = AzureOpenAILLMProvider(settings()).get_client()
        resp = llm.invoke([SystemMessage(content=_SUMMARY_SYSTEM),
                           HumanMessage(content=json.dumps(payload, ensure_ascii=False))])
        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            raw = raw[4:] if raw.lower().startswith("json") else raw
            raw = raw.strip()
        by_key = {str(x.get("key")): x for x in json.loads(raw) if isinstance(x, dict)}
        for r in subset:
            s = by_key.get(str(r["key"]), {})
            r["change_type"] = s.get("change_type", "")
            r["summary"] = s.get("summary", "")
    except Exception:  # noqa: BLE001 — summaries are a bonus; never fail the comparison
        for r in modified:
            r.setdefault("change_type", "")
            r.setdefault("summary", "")
