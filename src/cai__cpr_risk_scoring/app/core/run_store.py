"""Thread-safe in-memory store of run state.

Holds one `RunRecord` per run_id so the background job (writer) and the status/stream endpoints
(readers) share state. Finished runs are evicted oldest-first once `max_retained_runs` is exceeded.

NOTE: this is process-local. For multi-worker/multi-replica deployments, back it with Redis or the
observability DB — the `RunStore` interface is the single seam to swap.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.schemas.runs import Progress, RunState, RunStatus
from app.schemas.status_codes import ApiName


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RunRecord:
    run_id: str
    api: ApiName
    auditor_id: str
    state: RunState = RunState.PENDING
    status_code: str | None = None
    label: str | None = None
    progress: Progress | None = None
    result: dict | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    # Monotonically increasing version so SSE streams only push on change.
    version: int = 0

    def to_status(self) -> RunStatus:
        return RunStatus(
            run_id=self.run_id, api=self.api, auditor_id=self.auditor_id, state=self.state,
            status_code=self.status_code, label=self.label, progress=self.progress,
            result=self.result, error=self.error, started_at=self.started_at, updated_at=self.updated_at,
        )

    def is_terminal(self) -> bool:
        return self.state in (RunState.SUCCEEDED, RunState.FAILED)


class RunStore:
    """Concurrent map of run_id -> RunRecord with bounded retention of finished runs."""

    def __init__(self, max_retained: int = 200) -> None:
        self._lock = threading.RLock()
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._max_retained = max_retained

    def create(self, run_id: str, api: ApiName, auditor_id: str) -> RunRecord:
        with self._lock:
            record = RunRecord(run_id=run_id, api=api, auditor_id=auditor_id)
            self._runs[run_id] = record
            self._evict_if_needed()
            return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self, api: ApiName | None = None) -> list[RunRecord]:
        with self._lock:
            return [r for r in self._runs.values() if api is None or r.api == api]

    def update(self, run_id: str, **changes) -> RunRecord | None:
        """Apply field changes under the lock, bump version + updated_at. Returns the record or None."""
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return None
            for key, value in changes.items():
                setattr(record, key, value)
            record.updated_at = _now()
            record.version += 1
            return record

    def _evict_if_needed(self) -> None:
        # Evict oldest TERMINAL runs first; never evict a running/pending one.
        while len(self._runs) > self._max_retained:
            for rid, rec in list(self._runs.items()):
                if rec.is_terminal():
                    del self._runs[rid]
                    break
            else:
                break  # nothing terminal to evict yet


# Process-wide singleton (see module note on scaling).
_store: RunStore | None = None


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        from app.core.config import get_api_settings

        _store = RunStore(max_retained=get_api_settings().max_retained_runs)
    return _store
