"""Read-only Lakebase queries over the observability tables → list[dict] / dict.

Thin, cached accessors used by the level aggregations. Table + schema names come from the main app's
constants so they stay in lock-step with the recorder (public.obs_run / obs_question / obs_llm_call /
obs_finding / obs_ingestion).
"""

from __future__ import annotations

import time
from typing import Any

from backend.bootstrap import settings

from src.config.constants import (
    OBS_SCHEMA,
    OBS_TABLE_FINDING,
    OBS_TABLE_INGESTION,
    OBS_TABLE_LLM_CALL,
    OBS_TABLE_QUESTION,
    OBS_TABLE_RUN,
)

RUN = f"{OBS_SCHEMA}.{OBS_TABLE_RUN}"
QUESTION = f"{OBS_SCHEMA}.{OBS_TABLE_QUESTION}"
LLM_CALL = f"{OBS_SCHEMA}.{OBS_TABLE_LLM_CALL}"
FINDING = f"{OBS_SCHEMA}.{OBS_TABLE_FINDING}"
INGESTION = f"{OBS_SCHEMA}.{OBS_TABLE_INGESTION}"

_ALLOWED_LLM_DIMS = {"stage", "agent_name", "model", "prompt_name", "scope_area"}

# Tiny in-process TTL cache so rapid page loads don't re-hit the DB.
_CACHE: dict[tuple, tuple[float, Any]] = {}
_TTL = 45.0


def driver_ok() -> bool:
    from src.observability.lakebase import LakebaseConnection

    return LakebaseConnection.driver_available()


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    key = (sql, params)
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]
    from src.observability.lakebase import LakebaseConnection

    conn = LakebaseConnection(settings()).open()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            out = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()
    _CACHE[key] = (time.time(), out)
    return out


def clear_cache() -> None:
    _CACHE.clear()


def _exec(sql: str, params: tuple = ()) -> None:
    """Run a write (DDL/DML) and commit. Used only by remediation tracking."""
    from src.observability.lakebase import LakebaseConnection

    conn = LakebaseConnection(settings()).open()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ── Remediation tracking (open vs resolved high-risk) ───────────────────────────
REMEDIATION = f"{OBS_SCHEMA}.obs_remediation"
_VALID_STATUS = {"open", "in_progress", "resolved"}
_remediation_ready = False


def _ensure_remediation() -> None:
    global _remediation_ready
    if _remediation_ready:
        return
    _exec(
        f"CREATE TABLE IF NOT EXISTS {REMEDIATION} ("
        f"finding_uid TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'open', "
        f"note TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    _remediation_ready = True


def set_remediation(finding_uid: str, status: str, note: str = "") -> None:
    status = status if status in _VALID_STATUS else "open"
    _ensure_remediation()
    _exec(
        f"INSERT INTO {REMEDIATION} (finding_uid, status, note, updated_at) VALUES (%s, %s, %s, now()) "
        f"ON CONFLICT (finding_uid) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note, updated_at = now()",
        (finding_uid, status, note),
    )
    clear_cache()


def remediation_summary() -> dict:
    """Open / in-progress / resolved counts for HIGH-risk findings (findings with no row = 'open')."""
    _ensure_remediation()
    rows = _rows(
        f"SELECT COALESCE(r.status, 'open') AS status, COUNT(*) AS n "
        f"FROM {FINDING} f LEFT JOIN {REMEDIATION} r ON f.finding_uid::text = r.finding_uid "
        f"WHERE LOWER(COALESCE(f.risk_category, '')) LIKE 'high%%' GROUP BY 1"
    )
    out = {"open": 0, "in_progress": 0, "resolved": 0}
    for r in rows:
        out[str(r["status"])] = int(r["n"])
    return out


# ── Date-window helpers ──────────────────────────────────────────────────────────
def _date_clause(since: str | None, until: str | None, col: str = "started_at") -> tuple[str, tuple]:
    """(SQL fragment, params) for an optional [since, until] window on a timestamp column."""
    parts, params = [], []
    if since:
        parts.append(f"{col} >= %s")
        params.append(since)
    if until:
        parts.append(f"{col} < (%s::date + INTERVAL '1 day')")  # inclusive of the 'until' day
        params.append(until)
    return (" AND ".join(parts), tuple(params))


def _run_filter(run_ids: tuple | None) -> tuple[str, tuple]:
    """(SQL fragment, params) restricting an obs_* table to a set of run_ids (date-window result)."""
    if run_ids is None:
        return "", ()
    return "run_id::text = ANY(%s)", (list(run_ids),)


# ── Runs ────────────────────────────────────────────────────────────────────────
def runs(limit: int = 500, since: str | None = None, until: str | None = None) -> list[dict]:
    clause, params = _date_clause(since, until)
    where = f"WHERE {clause} " if clause else ""
    return _rows(f"SELECT * FROM {RUN} {where}ORDER BY started_at DESC LIMIT %s", params + (limit,))


def run_ids_in_window(since: str | None, until: str | None) -> tuple | None:
    """Tuple of run_ids within the window, or None when no window is set (→ all runs, unfiltered)."""
    if not since and not until:
        return None
    clause, params = _date_clause(since, until)
    rows = _rows(f"SELECT run_id::text AS run_id FROM {RUN} WHERE {clause}", params)
    return tuple(r["run_id"] for r in rows)


def run_detail(run_id: str) -> dict:
    head = _rows(f"SELECT * FROM {RUN} WHERE run_id = %s", (run_id,))
    return {
        "run": head[0] if head else None,
        "questions": _rows(f"SELECT * FROM {QUESTION} WHERE run_id = %s ORDER BY question_index", (run_id,)),
        "llm_calls": _rows(f"SELECT * FROM {LLM_CALL} WHERE run_id = %s ORDER BY started_at", (run_id,)),
        "findings": _rows(f"SELECT * FROM {FINDING} WHERE run_id = %s", (run_id,)),
        "ingestion": _rows(f"SELECT * FROM {INGESTION} WHERE run_id = %s ORDER BY started_at", (run_id,)),
    }


def _where(*clauses: str) -> str:
    kept = [c for c in clauses if c]
    return ("WHERE " + " AND ".join(kept) + " ") if kept else ""


# ── LLM analytics ────────────────────────────────────────────────────────────────
def llm_by(dim: str, run_ids: tuple | None = None) -> list[dict]:
    col = dim if dim in _ALLOWED_LLM_DIMS else "stage"
    rc, rp = _run_filter(run_ids)
    return _rows(
        f"SELECT COALESCE({col}, '(none)') AS {col}, COUNT(*) AS calls, "
        f"SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
        f"SUM(cached_tokens) AS cached_tokens, SUM(reasoning_tokens) AS reasoning_tokens, "
        f"SUM(total_tokens) AS tokens, SUM(cost_usd) AS cost_usd, AVG(latency_ms) AS avg_latency_ms, "
        f"SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors "
        f"FROM {LLM_CALL} {_where(rc)}GROUP BY 1 ORDER BY cost_usd DESC NULLS LAST", rp
    )


def llm_overview() -> dict:
    """Totals + per-dimension breakdowns for the dedicated LLM analytics page."""
    tot = _rows(
        f"SELECT COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
        f"SUM(cached_tokens) AS cached_tokens, SUM(reasoning_tokens) AS reasoning_tokens, "
        f"SUM(total_tokens) AS tokens, SUM(cost_usd) AS cost_usd, AVG(latency_ms) AS avg_latency_ms, "
        f"SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors FROM {LLM_CALL}"
    )
    return {
        "totals": tot[0] if tot else {},
        "by_stage": llm_by("stage"),
        "by_agent": llm_by("agent_name"),
        "by_model": llm_by("model"),
        "by_prompt": llm_by("prompt_name"),
    }


def questions_global(limit: int = 1000) -> list[dict]:
    """Per-question analytics across ALL runs (joined to the run's auditor) — one row per question."""
    return _rows(
        f"SELECT q.question_uid::text AS question_uid, q.run_id::text AS run_id, r.auditor_id, "
        f"q.question_index, q.source_file, q.assigned_scope, q.final_risk_category, q.final_matrix_score, "
        f"q.likelihood, q.impact, q.llm_confidence, q.derived_confidence, q.transactional_invoked, "
        f"q.status, q.num_llm_calls, q.input_tokens, q.output_tokens, q.total_tokens, q.cost_usd, q.duration_ms "
        f"FROM {QUESTION} q LEFT JOIN {RUN} r ON q.run_id = r.run_id "
        f"ORDER BY q.cost_usd DESC NULLS LAST LIMIT %s",
        (limit,),
    )


def llm_calls_for_question(question_uid: str) -> list[dict]:
    """Every LLM call for one question (the full chain) — for the per-question drill-down."""
    return _rows(
        f"SELECT stage, agent_name, prompt_name, prompt_version, model, temperature, attempt, status, "
        f"latency_ms, input_tokens, output_tokens, cached_tokens, reasoning_tokens, total_tokens, cost_usd, "
        f"request_payload, response_payload FROM {LLM_CALL} WHERE question_uid = %s ORDER BY started_at",
        (question_uid,),
    )


def findings_for_question(question_uid: str) -> list[dict]:
    """The finding(s) a question produced — links a per-question row to its risk output."""
    return _rows(
        f"SELECT finding_uid::text AS finding_uid, origin, scope_area, risk_type, risk_category, "
        f"likelihood, impact, matrix_score, is_recurrence, past_occurrence_ref "
        f"FROM {FINDING} WHERE question_uid = %s ORDER BY matrix_score DESC NULLS LAST",
        (question_uid,),
    )


def findings_by_category(run_ids: tuple | None = None) -> list[dict]:
    rc, rp = _run_filter(run_ids)
    return _rows(
        f"SELECT COALESCE(risk_category, '(none)') AS risk_category, COUNT(*) AS findings "
        f"FROM {FINDING} {_where(rc)}GROUP BY 1 ORDER BY findings DESC", rp
    )


def findings_by_scope(run_ids: tuple | None = None) -> list[dict]:
    rc, rp = _run_filter(run_ids)
    return _rows(
        f"SELECT COALESCE(scope_area, '(none)') AS scope_area, "
        f"COALESCE(risk_category, '(none)') AS risk_category, COUNT(*) AS findings "
        f"FROM {FINDING} {_where(rc)}GROUP BY 1, 2 ORDER BY findings DESC", rp
    )


def recurrence_count(run_ids: tuple | None = None) -> int:
    rc, rp = _run_filter(run_ids)
    r = _rows(f"SELECT COUNT(*) AS n FROM {FINDING} {_where('is_recurrence = true', rc)}", rp)
    return int(r[0]["n"]) if r else 0


def auditor_detail(auditor: str) -> dict:
    """Everything for one auditor — runs, finding mix, scope coverage, token/cost totals."""
    runs_ = _rows(
        f"SELECT run_id::text AS run_id, status, started_at, duration_ms, total_cost_usd, total_tokens, "
        f"findings_high, findings_medium, findings_low, questions_completed "
        f"FROM {RUN} WHERE auditor_id = %s ORDER BY started_at DESC", (auditor,))
    scopes = _rows(
        f"SELECT COALESCE(f.scope_area,'(none)') AS scope_area, COUNT(*) AS findings "
        f"FROM {FINDING} f JOIN {RUN} r ON f.run_id = r.run_id WHERE r.auditor_id = %s "
        f"GROUP BY 1 ORDER BY findings DESC LIMIT 20", (auditor,))
    cats = _rows(
        f"SELECT COALESCE(f.risk_category,'(none)') AS risk_category, COUNT(*) AS findings "
        f"FROM {FINDING} f JOIN {RUN} r ON f.run_id = r.run_id WHERE r.auditor_id = %s GROUP BY 1", (auditor,))
    return {"auditor": auditor, "runs": runs_, "by_scope": scopes, "by_category": cats}


def findings_for_scope(scope: str) -> list[dict]:
    """Finding rows for one scope area (with remediation status) — Risk-Overview drill-down."""
    _ensure_remediation()
    return _rows(
        f"SELECT f.finding_uid::text AS finding_uid, f.scope_area, f.risk_type, f.risk_category, "
        f"f.likelihood, f.impact, f.matrix_score, f.is_recurrence, "
        f"COALESCE(r.status, 'open') AS remediation_status "
        f"FROM {FINDING} f LEFT JOIN {REMEDIATION} r ON f.finding_uid::text = r.finding_uid "
        f"WHERE COALESCE(f.scope_area, '(none)') = %s ORDER BY f.matrix_score DESC NULLS LAST LIMIT 500",
        (scope,),
    )


def ingestion_by_status(run_ids: tuple | None = None) -> list[dict]:
    rc, rp = _run_filter(run_ids)
    return _rows(
        f"SELECT COALESCE(status, '(none)') AS status, COUNT(*) AS files, "
        f"SUM(num_chunks) AS chunks, SUM(cost_usd) AS cost_usd FROM {INGESTION} {_where(rc)}"
        f"GROUP BY 1 ORDER BY files DESC", rp
    )


def ingestion_by_index(run_ids: tuple | None = None) -> list[dict]:
    rc, rp = _run_filter(run_ids)
    return _rows(
        f"SELECT COALESCE(index_key, '(none)') AS index_key, COUNT(*) AS files, "
        f"SUM(num_chunks) AS chunks, SUM(total_tokens) AS tokens, SUM(cost_usd) AS cost_usd "
        f"FROM {INGESTION} {_where(rc)}GROUP BY 1 ORDER BY files DESC", rp
    )
