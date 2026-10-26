"""Risk Analysis job — drives the main FCPA audit pipeline (src.pipeline.runner.run_audit).

Emits the phase-level RA_* status codes around the run. run_audit is a single async call that performs
ingestion → transactional analysis → per-question processing → aggregation internally; fine-grained
per-phase / per-question emission (RA_Q_*) is wired by passing this run's reporter into the pipeline —
see ProgressReporter. For now the service marks the first and last phases and the terminal result.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.schemas.runs import StartRunRequest
from app.services.progress import ProgressReporter


async def run(reporter: ProgressReporter, request: StartRunRequest) -> None:
    from src.pipeline.runner import run_audit

    reporter.phase("RA_INGEST_SUPPORTING")  # the pipeline begins with reference/KB ingestion

    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"FCPA_Audit_{request.auditor_id}_{stamp}.xlsx"

    # Pass the reporter's phase callback so the pipeline publishes every RA_* phase (ingestion,
    # transactions, the per-question {current,total} counter, summary) at run time.
    await run_audit(request.auditor_id, output_path, on_phase=reporter.phase)

    from src.config.settings import get_settings
    from src.pipeline.output_upload import upload_output_file
    from src.repositories.delta_export import DeltaOutputExporter

    settings = get_settings()
    volume_path = upload_output_file(settings, output_path, label="output")

    # Delta tables the run writes (Output A/C/E/F, with _copy for A and C) — fully-qualified paths so the
    # caller gets both the volume output file AND the db tables once the run is done.
    db_tables = DeltaOutputExporter(settings).risk_analysis_output_tables()

    reporter.succeed({
        "output_path": str(output_path),
        "volume_path": volume_path,
        "db_tables": db_tables,
    })
