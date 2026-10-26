"""ProgressReporter — the single object a job uses to publish status to the run store.

A job calls `reporter.phase(code, current=, total=)` as it advances; the reporter resolves the label
and writes it to the shared RunStore. `succeed`/`fail` set the terminal state. This is the seam through
which the engines (src/, AI_scope/, Scoping_Deck/) can later emit per-step codes — pass the reporter
(or its `.phase` callback) down and call it.
"""

from __future__ import annotations

from app.core.run_store import RunStore
from app.schemas.runs import Progress, RunState
from app.schemas.status_codes import label_for
from src.utils.logger import get_logger

logger = get_logger("app.progress")


class ProgressReporter:
    def __init__(self, store: RunStore, run_id: str) -> None:
        self._store = store
        self._run_id = run_id

    def running(self) -> None:
        self._store.update(self._run_id, state=RunState.RUNNING)

    def phase(self, code: str, *, current: int | None = None, total: int | None = None) -> None:
        """Record the current phase (and optional loop progress)."""
        progress = Progress(current=current or 0, total=total or 0) if total else None
        self._store.update(
            self._run_id, state=RunState.RUNNING, status_code=code,
            label=label_for(code), progress=progress,
        )
        logger.info("[Run %s] %s | %s%s", self._run_id, code, label_for(code),
                    f" ({current}/{total})" if total else "")

    def succeed(self, result: dict | None = None) -> None:
        self._store.update(self._run_id, state=RunState.SUCCEEDED, result=result or {}, progress=None)
        logger.info("[Run %s] SUCCEEDED", self._run_id)

    def fail(self, error: str) -> None:
        self._store.update(self._run_id, state=RunState.FAILED, error=error)
        logger.error("[Run %s] FAILED | %s", self._run_id, error)
