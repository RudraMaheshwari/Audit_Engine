"""Scoping AI job — builds the AI Scope sheet (AI_scope engine) and writes the workbook.

Decomposed into the three AS_* phases: organize scopes → fetch sample size/category → summarize
per-scope risks. The engine calls are synchronous, so they run in a thread to avoid blocking the loop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from app.schemas.runs import StartRunRequest
from app.services.progress import ProgressReporter
from src.utils.logger import get_logger

logger = get_logger("app.scoping_ai")


async def run(reporter: ProgressReporter, request: StartRunRequest) -> None:
    from AI_scope.src.ai_scope_builder import build_ai_scope
    from AI_scope.src.config import load_config
    from AI_scope.src.output_writer import write_ai_scope

    reporter.phase("AS_ORGANIZE_SCOPES")
    config = await asyncio.to_thread(load_config)

    reporter.phase("AS_FETCH_SAMPLE_CATEGORY")
    rows = await asyncio.to_thread(build_ai_scope, config)

    reporter.phase("AS_SUMMARIZE_SCOPE_RISKS", current=0, total=len(rows))
    if not rows:
        reporter.succeed({"rows": 0})
        return

    llm = None
    try:
        from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider

        llm = AzureOpenAILLMProvider(config.settings).get_client()
    except Exception as exc:  # noqa: BLE001 — fall back to raw summaries if the LLM can't be built
        logger.warning("[ScopingAI] LLM unavailable for Sheet B structuring | %s", exc)

    out_dir = Path("AI_scope") / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"AI_Scope_{request.auditor_id}_{stamp}.xlsx"

    saved, summary_rows = await asyncio.to_thread(write_ai_scope, rows, out_path, llm)

    from src.pipeline.output_upload import upload_output_file
    volume_path = await asyncio.to_thread(
        upload_output_file, config.settings, saved, label="aiscope",
        dest_volume=config.settings.ai_scope_output_volume_path,
    )

    # Mirror the AI Scope SUMMARY (Sheet B — one row per scope) to a Delta table — AI_Scope_<eng> AND
    # AI_Scope_<eng>_copy — exactly like the CLI path, so Scoping Deck can read it as a db_table. The
    # API path previously only produced the Excel; this is the missing DB-table push. Best-effort.
    def _export_delta() -> str | None:
        from src.repositories.delta_export import DeltaOutputExporter

        headers = [
            "Scope Area", "Scope Decision", "Overall Risk Category",
            "Proposed Sample Size", "Summary of Risks", "Management/Past Audit Source Evidences",
        ]
        data = [list(values) for values in summary_rows]
        exporter = DeltaOutputExporter(config.settings)
        exporter.export_rows("AI_Scope", headers, data, also_copy=True)
        return (
            f"{config.settings.databricks_catalog}.{exporter._schema}."
            f"AI_Scope_{exporter._engagement_suffix()}"
        )

    delta_table = None
    try:
        delta_table = await asyncio.to_thread(_export_delta)
    except Exception as exc:  # noqa: BLE001 — Delta mirror is best-effort, never fails the run
        logger.warning("[ScopingAI] Delta export skipped | %s", exc)

    db_tables = (
        {"ai_scope": delta_table, "ai_scope_copy": f"{delta_table}_copy"} if delta_table else {}
    )
    reporter.succeed({
        "output_path": str(saved),
        "volume_path": volume_path,
        "delta_table": delta_table,
        "delta_table_copy": f"{delta_table}_copy" if delta_table else None,
        "db_tables": db_tables,
        "rows": len(rows),
    })
