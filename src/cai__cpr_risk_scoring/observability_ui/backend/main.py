"""FastAPI backend for the Observability UI.

Exposes the three dashboard lenses + the semantic-diff (with LLM change summaries) as JSON APIs the
Next.js frontend consumes. Reuses the main app's Lakebase / Databricks / embeddings / LLM via the
`backend.*` functions. Read-only.

Run from the observability_ui/ folder (so `backend` is importable):
    uvicorn backend.main:app --reload --port 8600
    # or from the project root:  uvicorn backend.main:app --app-dir observability_ui --reload --port 8600
"""

from __future__ import annotations

from backend import lakebase_queries as q
from backend import levels, semantic
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="AI Auditor — Observability API", version="1.0")

# The Next.js dev server runs on :3000; allow it (and any origin, since this is internal read-only).
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _guard(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — surface a clean 500 with the reason
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "lakebase_driver": q.driver_ok()}


@app.get("/api/config")
def config() -> dict:
    """Catalog + output schema so the UI can preview Delta table FQNs live (Change Review)."""
    s = semantic.settings()
    return {"catalog": getattr(s, "databricks_catalog", ""), "output_schema": semantic.output_schema()}


@app.get("/api/runs")
def runs(limit: int = Query(default=200, le=1000)) -> dict:
    return {"runs": _guard(lambda: q.runs(limit))}


@app.get("/api/findings")
def findings(scope: str = Query(..., description="Scope area to drill into")) -> dict:
    return {"scope": scope, "findings": _guard(lambda: q.findings_for_scope(scope))}


@app.get("/api/llm")
def llm() -> dict:
    return _guard(q.llm_overview)


@app.get("/api/questions")
def questions(limit: int = Query(default=1000, le=5000)) -> dict:
    return {"questions": _guard(lambda: q.questions_global(limit))}


@app.get("/api/questions/{question_uid}/calls")
def question_calls(question_uid: str) -> dict:
    return {
        "question_uid": question_uid,
        "calls": _guard(lambda: q.llm_calls_for_question(question_uid)),
        "findings": _guard(lambda: q.findings_for_question(question_uid)),
    }


@app.get("/api/auditors/{auditor}")
def auditor(auditor: str) -> dict:
    return _guard(lambda: q.auditor_detail(auditor))


class RemediationRequest(BaseModel):
    finding_uid: str
    status: str                # open | in_progress | resolved
    note: str = ""


@app.post("/api/remediation")
def remediation(req: RemediationRequest) -> dict:
    _guard(lambda: q.set_remediation(req.finding_uid, req.status, req.note))
    return {"ok": True, "finding_uid": req.finding_uid, "status": req.status}


@app.get("/api/technical")
def technical(since: str | None = Query(default=None), until: str | None = Query(default=None)) -> dict:
    return _guard(lambda: levels.technical(since, until))


@app.get("/api/user")
def user(since: str | None = Query(default=None), until: str | None = Query(default=None)) -> dict:
    return _guard(lambda: levels.user_level(since, until))


@app.get("/api/leadership")
def leadership(
    manual_hours: float | None = Query(default=None, description="Override: manual analyst hours per audit"),
    hourly: float | None = Query(default=None, description="Override: blended analyst cost per hour (USD)"),
    since: str | None = Query(default=None), until: str | None = Query(default=None),
) -> dict:
    return _guard(lambda: levels.leadership(manual_hours, hourly, since, until))


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    return _guard(lambda: q.run_detail(run_id))


@app.post("/api/refresh")
def refresh() -> dict:
    q.clear_cache()
    return {"ok": True}


class CompareRequest(BaseModel):
    source: str = "AI_Risk_Summary"      # table base, e.g. AI_Risk_Summary | AI_Scope
    engagement: str | None = None        # auto-builds <base>_<eng> + _copy
    orig_fqn: str | None = None          # or pass fully-qualified names directly
    copy_fqn: str | None = None
    key_col: str | None = None
    threshold: float = semantic.DEFAULT_SIM_THRESHOLD
    summarize: bool = True


@app.post("/api/semantic/compare")
def semantic_compare(req: CompareRequest) -> dict:
    if req.engagement and not (req.orig_fqn and req.copy_fqn):
        orig = semantic.fqn(req.source, req.engagement)
        copy = semantic.fqn(req.source, req.engagement, copy=True)
    else:
        orig, copy = req.orig_fqn, req.copy_fqn
    if not orig or not copy:
        raise HTTPException(status_code=400, detail="Provide engagement, or both orig_fqn and copy_fqn.")
    result = _guard(lambda: semantic.compare(orig, copy, req.key_col, req.threshold, req.summarize))
    result["tables"] = {"original": orig, "copy": copy}
    return result
