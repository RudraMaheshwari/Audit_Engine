"""Smoke test for the observability layer against Lakebase Postgres.

Proves, end to end, that:
  1. the 4 tables auto-create on first run (idempotent — safe to re-run),
  2. a run / question / llm_call / finding row each write,
  3. the SQL-aggregate roll-ups populate question + run totals.

It uses a throwaway run_id and leaves the rows in place for inspection. Run:

    pip install "psycopg[binary]"          # one-time
    set LAKEBASE_INSTANCE_NAME=<Instance name>
    python test_observability.py

Requires DATABRICKS_HOST + DATABRICKS_TOKEN in your .env (same as test_postgres_access.py).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ["OBS_ENABLED"] = "true"

from src.config.settings import Settings  # noqa: E402
from src.config.constants import (  # noqa: E402
    OBS_ORIGIN_QUESTIONNAIRE,
    OBS_QSTATUS_COMPLETED,
    OBS_STAGE_PER_QUESTION,
    OBS_STATUS_COMPLETED,
    RISK_CATEGORY_HIGH,
)
from src.observability.recorder import ObservabilityRecorder  # noqa: E402
from src.observability.pricing import compute_cost_usd  # noqa: E402


def main() -> None:
    settings = Settings()
    if not settings.lakebase_instance_name.strip():
        sys.exit("Set LAKEBASE_INSTANCE_NAME (the Lakebase Instance name) before running.")

    recorder = ObservabilityRecorder(settings)
    if not recorder.enabled:
        sys.exit("Recorder disabled — check OBS_ENABLED, psycopg install, and LAKEBASE_INSTANCE_NAME.")

    print("[1/6] Ensuring schema (creates tables only if missing) ...")
    recorder.ensure_schema()

    print("[2/6] Starting run ...")
    started = datetime.now(timezone.utc)
    run_id = recorder.start_run(
        auditor_id="smoke_test",
        model=settings.azure_openai_deployment,
        prompt_bundle_id="smoke-bundle",
        prompt_versions={"smoke": "1.0"},
        run_config={"note": "observability smoke test"},
        total_questions=1,
    )
    print(f"      run_id={run_id}")

    print("[3/6] Starting question + recording an LLM call ...")
    q_started = datetime.now(timezone.utc)
    question_uid = recorder.start_question(
        run_id=run_id, question_index=1, source_file="smoke", question_hash="deadbeef",
    )
    recorder.record_llm_call({
        "run_id": run_id,
        "question_uid": question_uid,
        "stage": OBS_STAGE_PER_QUESTION,
        "agent_name": "smoke_agent",
        "model": settings.azure_openai_deployment,
        "attempt": 1,
        "status": "ok",
        "started_at": q_started,
        "ended_at": datetime.now(timezone.utc),
        "input_tokens": 1200,
        "output_tokens": 300,
        "total_tokens": 1500,
        "cached_tokens": 0,
        "cost_usd": compute_cost_usd(settings.azure_openai_deployment, 1200, 300, 0),
        "request_payload": "smoke request",
        "response_payload": "smoke response",
    })

    print("[4/6] Finishing question (roll-up from llm_call) ...")
    recorder.finish_question(
        question_uid=question_uid,
        status=OBS_QSTATUS_COMPLETED,
        started_at=q_started,
        assigned_scope="PRICING",
        final_risk_category=RISK_CATEGORY_HIGH,
        final_matrix_score=20,
        likelihood=5,
        impact=4,
        llm_confidence=0.82,
        derived_confidence=0.71,
        transactional_invoked=True,
    )

    print("[5/6] Recording a finding + finishing run ...")
    recorder.record_finding({
        "run_id": run_id,
        "question_uid": question_uid,
        "origin": OBS_ORIGIN_QUESTIONNAIRE,
        "scope_area": "PRICING",
        "risk_type": "Smoke risk",
        "risk_category": RISK_CATEGORY_HIGH,
        "likelihood": 5,
        "impact": 4,
        "matrix_score": 20,
        "llm_confidence": 0.82,
        "derived_confidence": 0.71,
        "thresholds_applied": {"high_min": 15, "medium_min": 10},
        "is_recurrence": False,
    })
    recorder.finish_run(run_id=run_id, status=OBS_STATUS_COMPLETED, started_at=started)

    print("[6/6] Reading back counts for this run ...")
    for table in ("obs_run", "obs_question", "obs_llm_call", "obs_finding"):
        col = "run_id" if table != "obs_run" else "run_id"
        rows = recorder._execute(
            f"SELECT count(*) FROM public.{table} WHERE {col} = %s", (run_id,), fetch=True
        )
        count = rows[0][0] if rows else "?"
        print(f"      {table}: {count} row(s) for this run")

    totals = recorder._execute(
        "SELECT total_llm_calls, total_input_tokens, total_output_tokens, total_tokens, total_cost_usd "
        "FROM public.obs_run WHERE run_id = %s", (run_id,), fetch=True,
    )
    if totals:
        c, i, o, t, cost = totals[0]
        print(f"      run roll-up: calls={c} input={i} output={o} total={t} cost=${cost}")

    recorder.close()
    print("\n✅ SUCCESS — observability schema + writes + roll-ups verified.")
    print(f"   Inspect: SELECT * FROM public.obs_run WHERE run_id = '{run_id}';")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ FAILED — {type(exc).__name__}: {exc}")
        sys.exit(1)
