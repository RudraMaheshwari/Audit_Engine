"""Generic background-run orchestration — shared by all three APIs.

Creates the run record, schedules the (async) job on the event loop, applies per-run engine config,
and guarantees a terminal state + error capture. Keeps the routes thin: they just call `start(...)`.

CONSTRAINT: the engine reads a process-global `src.config.settings` singleton, so two runs for
DIFFERENT auditors must not overlap. `start()` therefore rejects a new run while another is active for
a different auditor (409). Same-auditor concurrency is also blocked to avoid duplicated work.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Awaitable, Callable

from app.core.exceptions import ConflictError
from app.core.run_store import RunStore, get_run_store
from app.schemas.runs import RunState, StartRunRequest
from app.schemas.status_codes import ApiName
from app.services.progress import ProgressReporter
from src.utils.logger import get_logger

logger = get_logger("app.run_manager")

# A job: given a reporter + the request, drive the engine and report progress. Must be awaitable.
JobFn = Callable[[ProgressReporter, StartRunRequest], Awaitable[None]]


class RunManager:
    def __init__(self, store: RunStore) -> None:
        self._store = store

    def start(self, api: ApiName, request: StartRunRequest, job: JobFn) -> str:
        self._guard_single_active(request)
        run_id = str(uuid.uuid4())
        self._store.create(run_id, api, request.auditor_id)
        asyncio.create_task(self._execute(run_id, api, request, job))
        logger.info("[RunManager] Started %s run | run_id=%s | auditor=%s", api.value, run_id, request.auditor_id)
        return run_id

    def _guard_single_active(self, request: StartRunRequest) -> None:
        active = [r for r in self._store.list() if r.state in (RunState.PENDING, RunState.RUNNING)]
        if active:
            other = active[0]
            raise ConflictError(
                "A run is already in progress; wait for it to finish before starting another.",
                details={"active_run_id": other.run_id, "active_auditor": other.auditor_id,
                         "active_api": other.api.value},
            )

    async def _execute(self, run_id: str, api: ApiName, request: StartRunRequest, job: JobFn) -> None:
        reporter = ProgressReporter(self._store, run_id)
        try:
            self._apply_run_env(request)
            reporter.running()
            await job(reporter, request)
            record = self._store.get(run_id)
            if record and not record.is_terminal():     # job didn't set a terminal state itself
                reporter.succeed()
        except Exception as exc:  # noqa: BLE001 — capture ANY engine failure as a failed run
            logger.error("[RunManager] Run failed | run_id=%s | %s", run_id, exc, exc_info=True)
            reporter.fail(str(exc))

    @staticmethod
    def _apply_run_env(request: StartRunRequest) -> None:
        """Map the request onto the engine's settings (via env vars that get_settings reads), then
        invalidate the cached settings. Only fields the request provides are set — anything omitted
        keeps the engine's configured default (env / derived from auditor_id)."""
        os.environ["AUDITOR_ID"] = request.auditor_id
        if request.engagement_numbers:
            os.environ["AUDIT_ENGAGEMENT_NUMBERS"] = request.engagement_numbers

        # Questionnaire tables + ingestion folders are Risk-Analysis-only fields (absent on Scoping AI /
        # Deck requests) — access them getattr-safe so the factory works for every request type.
        questionnaire = getattr(request, "questionnaire", None)
        if questionnaire:
            if questionnaire.idr:
                os.environ["IDR_TABLES"] = ",".join(questionnaire.idr)
                os.environ["IDR_TABLE"] = questionnaire.idr[0]
            if questionnaire.planning:
                os.environ["PLANNING_TABLES"] = ",".join(questionnaire.planning)
                os.environ["PLANNING_TABLE_NAME"] = questionnaire.planning[0]

        # Per-run ingestion folders (explicit full paths; omitted -> engine derives base + auditor_id).
        for value, env_var in (
            (getattr(request, "supporting_docs_path", None), "SUPPORTING_DOCS_PATH"),
            (getattr(request, "si_history_path", None), "SI_HISTORY_PATH"),
            (getattr(request, "legal_path", None), "LEGAL_PATH"),
            (getattr(request, "additional_input_path", None), "ADDITIONAL_INPUT_PATH"),
            (getattr(request, "past_audit_path", None), "PAST_AUDIT_PATH"),
            (getattr(request, "tpi_inventory_path", None), "TPI_INVENTORY_FILE_PATH"),
        ):
            if value:
                os.environ[env_var] = value

        # Scoping AI + Scoping Deck inputs (present only on their request subclasses — getattr-safe).
        # These configs read env, so the factory maps the request onto the env vars they expect.
        def _g(name):
            return getattr(request, name, None)

        _scoping_env = {
            # Scoping AI
            "AI_SCOPE_SOURCE": _g("source") if _g("path") else None,  # AI-scope source pairs with path
            "AI_SCOPE_PATH": _g("path"),
            "AI_SCOPE_OUTPUT_E_PATH": _g("output_e_path"),  # Output E (Past Audit) table — db_table only
            "AI_SCOPE_OUTPUT_F_PATH": _g("output_f_path"),  # Output F (Scope Txn) table — db_table only
            # Scoping Deck
            "SCOPING_DECK_ENTITY_NAME": _g("entity_name"),
            "SCOPING_DECK_COUNTRY": _g("country"),
            "SCOPING_DECK_ISO3": _g("iso3"),
            "SCOPING_DECK_MRC_NUMBER": _g("mrc_number"),
            "SCOPING_DECK_SCOPE_PERIOD_START": _g("scope_period_start").isoformat() if _g("scope_period_start") else None,
            "SCOPING_DECK_SCOPE_PERIOD_END": _g("scope_period_end").isoformat() if _g("scope_period_end") else None,
            "SCOPING_DECK_RISK_SCOPE_PATH": _g("risk_scope_path"),
            "SCOPING_DECK_PAST_REVIEW_PATH": _g("past_review_path"),
            "SCOPING_DECK_OUTPUT_F_PATH": _g("output_f_path"),  # Output F table — db_table source only
            # The deck's `source` governs BOTH its table/excel reads (risk-scope + past-review).
            "SCOPING_DECK_PAST_REVIEW_SOURCE": _g("source") if _g("past_review_path") else None,
            "SCOPING_DECK_RISK_SCOPE_SOURCE": _g("source") if _g("risk_scope_path") else None,
        }
        for env_var, value in _scoping_env.items():
            if value:
                os.environ[env_var] = str(value)

        try:
            from src.config.settings import get_settings

            if hasattr(get_settings, "cache_clear"):
                get_settings.cache_clear()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RunManager] Could not refresh settings cache | %s", exc)


_manager: RunManager | None = None


def get_run_manager() -> RunManager:
    global _manager
    if _manager is None:
        _manager = RunManager(get_run_store())
    return _manager
