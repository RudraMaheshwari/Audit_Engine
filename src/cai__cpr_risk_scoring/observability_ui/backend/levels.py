"""Three observability lenses, as pure functions returning JSON-serializable dicts.

  technical()   — engineering/ops: runs, tokens, cost, latency, LLM breakdowns, ingestion.
  user_level()  — audit outcomes: of all runs, how many are high/medium/low risk; findings mix; coverage.
  leadership()  — business/ROI: audits delivered, risk caught, cost efficiency, estimated effort saved.

The level NAMES are intentionally not surfaced in the API responses (the UI labels them). Assumptions
used by the leadership ROI are explicit + overridable so the number is defensible, never a black box.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from backend import lakebase_queries as q


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    return int(_f(v, default))


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, datetime) else str(v)


def _quarter_key(v: Any) -> str | None:
    if not isinstance(v, datetime):
        return None
    return f"{v.year}-Q{(v.month - 1) // 3 + 1}"


def _pct_delta(cur: float, prev: float) -> float | None:
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _series(runs: list[dict], field: str) -> list[dict]:
    """[{t, value, auditor, status}] ordered by time, for a per-run metric."""
    out = []
    for r in sorted(runs, key=lambda x: str(x.get("started_at") or "")):
        out.append({"t": _iso(r.get("started_at")), "value": _f(r.get(field)),
                    "auditor": r.get("auditor_id"), "status": r.get("status")})
    return out


def _run_risk_band(r: dict) -> str:
    """Classify a whole run by its most severe finding count (for 'how many runs are high risk')."""
    if _i(r.get("findings_high")) > 0:
        return "High"
    if _i(r.get("findings_medium")) > 0:
        return "Medium"
    if _i(r.get("findings_low")) > 0:
        return "Low"
    return "No Risk"


# ──────────────────────────────────────────────────────────────────────────────
def technical(since: str | None = None, until: str | None = None) -> dict:
    rids = q.run_ids_in_window(since, until)
    runs = q.runs(since=since, until=until)
    kpis = {
        "runs": len(runs),
        "completed": sum(1 for r in runs if r.get("status") == "completed"),
        "failed": sum(1 for r in runs if r.get("status") == "failed"),
        "total_cost_usd": round(sum(_f(r.get("total_cost_usd")) for r in runs), 2),
        "total_tokens": sum(_i(r.get("total_tokens")) for r in runs),
        "total_llm_calls": sum(_i(r.get("total_llm_calls")) for r in runs),
        "avg_cost_per_run": round(sum(_f(r.get("total_cost_usd")) for r in runs) / len(runs), 2) if runs else 0,
        "avg_duration_ms": round(sum(_f(r.get("duration_ms")) for r in runs) / len(runs), 0) if runs else 0,
    }
    return {
        "kpis": kpis,
        "cost_series": _series(runs, "total_cost_usd"),
        "token_series": [
            {"t": _iso(r.get("started_at")), "input": _f(r.get("total_input_tokens")),
             "output": _f(r.get("total_output_tokens"))}
            for r in sorted(runs, key=lambda x: str(x.get("started_at") or ""))
        ],
        "llm_by_stage": q.llm_by("stage", rids),
        "llm_by_model": q.llm_by("model", rids),
        "ingestion_by_status": q.ingestion_by_status(rids),
        "ingestion_by_index": q.ingestion_by_index(rids),
        "recent_runs": [
            {k: _iso(r.get(k)) if k == "started_at" else r.get(k)
             for k in ("run_id", "auditor_id", "status", "started_at", "duration_ms",
                       "total_cost_usd", "total_tokens", "total_llm_calls")}
            for r in runs[:50]
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
def user_level(since: str | None = None, until: str | None = None) -> dict:
    rids = q.run_ids_in_window(since, until)
    runs = q.runs(since=since, until=until)
    bands = {"High": 0, "Medium": 0, "Low": 0, "No Risk": 0}
    for r in runs:
        bands[_run_risk_band(r)] += 1

    findings = {
        "high": sum(_i(r.get("findings_high")) for r in runs),
        "medium": sum(_i(r.get("findings_medium")) for r in runs),
        "low": sum(_i(r.get("findings_low")) for r in runs),
        "no_risk": sum(_i(r.get("findings_no_risk")) for r in runs),
    }
    q_tot = sum(_i(r.get("total_questions")) for r in runs)
    q_done = sum(_i(r.get("questions_completed")) for r in runs)
    q_skip = sum(_i(r.get("questions_skipped")) for r in runs)
    q_err = sum(_i(r.get("questions_error")) for r in runs)
    scope = q.findings_by_scope(rids)
    scopes_covered = len({s["scope_area"] for s in scope if s.get("scope_area") not in (None, "(none)")})

    return {
        "kpis": {
            "runs": len(runs),
            "high_risk_runs": bands["High"],
            "total_findings": sum(findings.values()),
            "high_findings": findings["high"],
            "scopes_covered": scopes_covered,
            "recurrence_findings": q.recurrence_count(rids),
            "questions_reviewed": q_tot,
        },
        "runs_by_band": [{"band": k, "runs": v} for k, v in bands.items()],
        "findings_by_severity": [
            {"severity": "High", "count": findings["high"]},
            {"severity": "Medium", "count": findings["medium"]},
            {"severity": "Low", "count": findings["low"]},
            {"severity": "No Risk", "count": findings["no_risk"]},
        ],
        "findings_by_scope": scope,
        "questions": {"total": q_tot, "completed": q_done, "skipped": q_skip, "error": q_err},
        "by_auditor": _by_auditor(runs),
    }


def _by_auditor(runs: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in runs:
        a = r.get("auditor_id") or "(unknown)"
        d = agg.setdefault(a, {"auditor": a, "runs": 0, "high": 0, "medium": 0, "low": 0})
        d["runs"] += 1
        d["high"] += _i(r.get("findings_high"))
        d["medium"] += _i(r.get("findings_medium"))
        d["low"] += _i(r.get("findings_low"))
    return sorted(agg.values(), key=lambda x: x["high"], reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
def leadership(manual_hours: float | None = None, hourly: float | None = None,
               since: str | None = None, until: str | None = None) -> dict:
    """Business value / ROI. Assumptions are explicit and tunable so the number is defensible.

    Override order: runtime args (set live from the UI) → env (OBS_MANUAL_HOURS_PER_AUDIT /
    OBS_ANALYST_HOURLY_USD) → built-in defaults (40 analyst hours/audit, $120/hour).
    """
    rids = q.run_ids_in_window(since, until)
    runs = q.runs(since=since, until=until)
    completed = [r for r in runs if r.get("status") == "completed"]
    total_cost = round(sum(_f(r.get("total_cost_usd")) for r in runs), 2)
    high = sum(_i(r.get("findings_high")) for r in runs)
    total_findings = sum(_i(r.get("findings_high")) + _i(r.get("findings_medium"))
                         + _i(r.get("findings_low")) for r in runs)

    manual_hours = manual_hours if manual_hours and manual_hours > 0 else \
        _f(os.getenv("OBS_MANUAL_HOURS_PER_AUDIT", "40"), 40)
    hourly = hourly if hourly and hourly > 0 else _f(os.getenv("OBS_ANALYST_HOURLY_USD", "120"), 120)
    n_audits = len(completed) or len(runs)
    hours_saved = manual_hours * n_audits
    manual_cost = hours_saved * hourly
    roi_pct = round((manual_cost - total_cost) / total_cost * 100, 1) if total_cost > 0 else None

    # Audit-program effectiveness / posture (web-informed: coverage, findings/audit, KRIs, efficiency).
    scope_rows = q.findings_by_scope(rids)
    scopes_covered = len({s["scope_area"] for s in scope_rows if s.get("scope_area") not in (None, "(none)")})
    recurrence = q.recurrence_count(rids)
    rem = q.remediation_summary()
    unresolved_high = rem["open"] + rem["in_progress"]
    avg_cycle_min = round(sum(_f(r.get("duration_ms")) for r in runs) / len(runs) / 60000, 1) if runs else 0

    # Quarterly trend — audits, spend, high-risk caught, ROI per quarter.
    qmap: dict[str, dict] = {}
    for r in runs:
        qk = _quarter_key(r.get("started_at"))
        if not qk:
            continue
        d = qmap.setdefault(qk, {"quarter": qk, "audits": 0, "spend": 0.0, "high": 0, "findings": 0})
        d["audits"] += 1
        d["spend"] += _f(r.get("total_cost_usd"))
        d["high"] += _i(r.get("findings_high"))
        d["findings"] += _i(r.get("findings_high")) + _i(r.get("findings_medium")) + _i(r.get("findings_low"))
    quarterly = []
    for d in sorted(qmap.values(), key=lambda x: x["quarter"]):
        d["spend"] = round(d["spend"], 2)
        d["est_value"] = round(d["audits"] * manual_hours * hourly, 0)
        d["roi_pct"] = round((d["est_value"] - d["spend"]) / d["spend"] * 100, 1) if d["spend"] > 0 else None
        quarterly.append(d)

    # Quarter-over-quarter deltas for the headline KPIs (last full quarter vs the one before).
    qoq = {}
    if len(quarterly) >= 2:
        cur, prev = quarterly[-1], quarterly[-2]
        qoq = {
            "quarter": cur["quarter"],
            "prev_quarter": prev["quarter"],
            "audits_delta_pct": _pct_delta(cur["audits"], prev["audits"]),
            "high_delta_pct": _pct_delta(cur["high"], prev["high"]),
            "spend_delta_pct": _pct_delta(cur["spend"], prev["spend"]),
        }

    return {
        "kpis": {
            "audits_delivered": len(completed),
            "high_risk_identified": high,
            "total_findings": total_findings,
            "ai_spend_usd": total_cost,
            "cost_per_audit": round(total_cost / n_audits, 2) if n_audits else 0,
            "cost_per_high_risk": round(total_cost / high, 2) if high else None,
            "est_hours_saved": int(hours_saved),
            "est_value_usd": round(manual_cost, 0),
            "roi_pct": roi_pct,
            "findings_per_audit": round(total_findings / n_audits, 1) if n_audits else 0,
            "high_risk_rate_pct": round(high / total_findings * 100, 1) if total_findings else 0,
            "scopes_covered": scopes_covered,
            "recurrence_findings": recurrence,
            "avg_cycle_minutes": avg_cycle_min,
            "unresolved_high_risk": unresolved_high,
        },
        "remediation": [
            {"status": "Open", "count": rem["open"]},
            {"status": "In progress", "count": rem["in_progress"]},
            {"status": "Resolved", "count": rem["resolved"]},
        ],
        "quarterly": quarterly,
        "qoq": qoq,
        "assumptions": {
            "manual_hours_per_audit": manual_hours,
            "analyst_hourly_usd": hourly,
            "note": "ROI = (estimated manual cost − AI spend) ÷ AI spend. Adjust the assumptions via "
                    "OBS_MANUAL_HOURS_PER_AUDIT / OBS_ANALYST_HOURLY_USD.",
        },
        "spend_vs_value": [
            {"label": "AI spend", "usd": total_cost},
            {"label": "Est. manual cost", "usd": round(manual_cost, 0)},
        ],
        "audits_over_time": _series(runs, "total_cost_usd"),
        "risk_mix": q.findings_by_category(rids),
        "top_scopes": scope_rows[:12],
    }
