"""ObservabilityRecorder — writes run / question / llm_call / finding rows to Lakebase Postgres.

Design guarantees:
  * Never raises into the audit pipeline. Every public method is wrapped so a Postgres failure logs
    a warning and degrades to a no-op — the audit run continues unaffected.
  * Idempotent schema. ensure_schema() runs CREATE TABLE IF NOT EXISTS, so the tables are created on
    the first run and left untouched afterwards.
  * Thread-safe. A single connection is guarded by a lock, so the concurrent question workers and
    the transactional thread pool can record safely.
  * Accurate roll-ups. Question and run totals are computed with SQL aggregates over the child rows,
    so the numbers always reconcile with the individual llm_call rows.

Rows are written immediately (commit per write) so a crash still leaves a complete partial trace.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config.constants import (
    OBS_QSTATUS_COMPLETED,
    OBS_QSTATUS_ERROR,
    OBS_QSTATUS_SKIPPED,
    OBS_INGEST_COMPLETED,
    OBS_ORIGIN_AGGREGATED,
    OBS_SCHEMA,
    OBS_STATUS_COMPLETED,
    OBS_STATUS_RUNNING,
    OBS_TABLE_FINDING,
    OBS_TABLE_INGESTION,
    OBS_TABLE_LLM_CALL,
    OBS_TABLE_QUESTION,
    OBS_TABLE_RUN,
    RISK_CATEGORY_HIGH,
    RISK_CATEGORY_LOW,
    RISK_CATEGORY_MEDIUM,
    RISK_CATEGORY_NONE,
)
from src.config.settings import Settings
from src.observability.lakebase import LakebaseConnection
from src.observability.pricing import compute_cost_usd
from src.observability.schema import MIGRATION_STATEMENTS, SCHEMA_STATEMENTS
from src.utils.logger import get_logger

logger = get_logger(__name__)

_RUN = f"{OBS_SCHEMA}.{OBS_TABLE_RUN}"
_QUESTION = f"{OBS_SCHEMA}.{OBS_TABLE_QUESTION}"
_LLM_CALL = f"{OBS_SCHEMA}.{OBS_TABLE_LLM_CALL}"
_FINDING = f"{OBS_SCHEMA}.{OBS_TABLE_FINDING}"
_INGESTION = f"{OBS_SCHEMA}.{OBS_TABLE_INGESTION}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return int((end - start).total_seconds() * 1000)


class ObservabilityRecorder:
    """Persists observability rows for one pipeline run to Lakebase Postgres."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lakebase = LakebaseConnection(settings)
        self._conn = None
        self._lock = threading.Lock()
        self._enabled = self._resolve_enabled()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _resolve_enabled(self) -> bool:
        if not self._settings.obs_enabled:
            return False
        if not LakebaseConnection.driver_available():
            logger.warning("[Obs] Disabled — psycopg driver not installed (pip install 'psycopg[binary]').")
            return False
        if not self._settings.lakebase_instance_name.strip():
            logger.warning("[Obs] Disabled — lakebase_instance_name is not set.")
            return False
        return True

    def _truncate(self, text: str | None) -> str | None:
        if text is None:
            return None
        limit = self._settings.obs_payload_max_chars
        if limit and limit > 0 and len(text) > limit:
            return text[:limit]
        return text

    def _connect(self) -> None:
        self._conn = self._lakebase.open()

    def _execute(self, sql: str, params: tuple | None = None, fetch: bool = False, quiet: bool = False):
        """Run a statement under the lock.

        A STATEMENT error (permission / bad column / constraint) is deterministic: we roll back to
        clear the aborted transaction so the SHARED connection stays usable, log once, and return
        WITHOUT retrying or reconnecting (retrying would just spam the log and reopen the connection —
        the cause of the old 'must be owner' flood). Only a genuine CONNECTION drop triggers a close +
        reconnect + one retry. `quiet=True` downgrades the failure log to DEBUG (best-effort DDL that
        legitimately fails for a non-owner role)."""
        log = logger.debug if quiet else logger.warning
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._connect()
                    with self._conn.cursor() as cur:
                        cur.execute(sql, params or ())
                        result = cur.fetchall() if fetch else None
                    self._conn.commit()
                    return result
                except Exception as exc:  # noqa: BLE001
                    # If rollback succeeds the connection is alive → this was a statement error, not a
                    # dropped connection. Don't reconnect or retry; the next statement reuses the conn.
                    conn_alive = False
                    try:
                        if self._conn is not None and not getattr(self._conn, "closed", False):
                            self._conn.rollback()
                            conn_alive = True
                    except Exception:
                        conn_alive = False
                    if conn_alive:
                        log("[Obs] DB op failed | error=%s", exc)
                        return None
                    # Connection looks broken — close, reconnect, retry once.
                    log("[Obs] DB connection lost (attempt %d) — reconnecting | error=%s", attempt, exc)
                    try:
                        if self._conn is not None:
                            self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                    if attempt == 2:
                        return None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        if not self._enabled:
            return
        # Base tables: CREATE TABLE IF NOT EXISTS is a no-op on an existing table and does NOT require
        # ownership, so this succeeds for any role that can connect.
        for statement in SCHEMA_STATEMENTS:
            self._execute(statement)
        # Migrations + indexes: need table ownership; a non-owner role fails with "must be owner" which
        # is harmless (columns already exist in the base DDL). Run them QUIETLY — no warnings, no retry.
        for statement in MIGRATION_STATEMENTS:
            self._execute(statement, quiet=True)
        logger.info("[Obs] Schema ensured (tables created if missing; migrations best-effort).")

    def start_run(
        self,
        *,
        auditor_id: str,
        model: str,
        prompt_bundle_id: str,
        prompt_versions: dict[str, Any],
        run_config: dict[str, Any],
        total_questions: int | None = None,
    ) -> str | None:
        if not self._enabled:
            return None
        run_id = str(uuid.uuid4())
        self._execute(
            f"""
            INSERT INTO {_RUN}
                (run_id, auditor_id, status, started_at, model, prompt_bundle_id,
                 prompt_versions, run_config, app_version, total_questions)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, auditor_id, OBS_STATUS_RUNNING, _now(), model, prompt_bundle_id,
                json.dumps(prompt_versions), json.dumps(run_config),
                self._settings.app_version, total_questions,
            ),
        )
        logger.info("[Obs] Run started | run_id=%s | auditor=%s", run_id, auditor_id)
        return run_id

    def start_question(
        self,
        *,
        run_id: str,
        question_index: int | None,
        source_file: str | None,
        question_hash: str | None,
        assigned_scope: str | None = None,
    ) -> str | None:
        if not self._enabled or not run_id:
            return None
        question_uid = str(uuid.uuid4())
        self._execute(
            f"""
            INSERT INTO {_QUESTION}
                (question_uid, run_id, question_index, source_file, question_hash,
                 assigned_scope, status, started_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                question_uid, run_id, question_index, source_file, question_hash,
                assigned_scope, OBS_STATUS_RUNNING, _now(),
            ),
        )
        return question_uid

    def finish_question(
        self,
        *,
        question_uid: str | None,
        status: str,
        started_at: datetime | None = None,
        assigned_scope: str | None = None,
        final_risk_category: str | None = None,
        final_matrix_score: int | None = None,
        likelihood: int | None = None,
        impact: int | None = None,
        llm_confidence: float | None = None,
        derived_confidence: float | None = None,
        transactional_invoked: bool | None = None,
        error: str | None = None,
    ) -> None:
        if not self._enabled or not question_uid:
            return
        ended = _now()
        self._execute(
            f"""
            UPDATE {_QUESTION} SET
                status = %s,
                assigned_scope = COALESCE(%s, assigned_scope),
                final_risk_category = %s,
                final_matrix_score = %s,
                likelihood = %s,
                impact = %s,
                llm_confidence = %s,
                derived_confidence = %s,
                transactional_invoked = %s,
                error = %s,
                ended_at = %s,
                duration_ms = %s,
                num_llm_calls     = (SELECT count(*)             FROM {_LLM_CALL} WHERE question_uid = %s),
                input_tokens      = (SELECT COALESCE(sum(input_tokens), 0)  FROM {_LLM_CALL} WHERE question_uid = %s),
                output_tokens     = (SELECT COALESCE(sum(output_tokens), 0) FROM {_LLM_CALL} WHERE question_uid = %s),
                total_tokens      = (SELECT COALESCE(sum(total_tokens), 0)  FROM {_LLM_CALL} WHERE question_uid = %s),
                cost_usd          = (SELECT COALESCE(sum(cost_usd), 0)      FROM {_LLM_CALL} WHERE question_uid = %s)
            WHERE question_uid = %s
            """,
            (
                status, assigned_scope, final_risk_category, final_matrix_score,
                likelihood, impact, llm_confidence, derived_confidence, transactional_invoked,
                error, ended, _duration_ms(started_at, ended),
                question_uid, question_uid, question_uid, question_uid, question_uid,
                question_uid,
            ),
        )

    def set_total_questions(self, run_id: str | None, count: int) -> None:
        if not self._enabled or not run_id:
            return
        self._execute(f"UPDATE {_RUN} SET total_questions = %s WHERE run_id = %s", (count, run_id))

    def record_ingestion(
        self,
        *,
        run_id: str | None,
        index_key: str | None,
        document_type: str | None,
        document_source: str | None,
        source_file: str | None,
        folder: str | None,
        scope_id: str | None,
        status: str,
        num_chunks: int,
        total_tokens: int,
        embedding_model: str | None,
        embedding_dimensions: int | None,
        error: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        """Write one obs_ingestion row per file (download → extract → chunk → embed → upsert).

        num_embeddings equals num_chunks on a completed row (one embedding per chunk) and 0 otherwise.
        Thread-safe via _execute's lock, so it is safe to call from the parallel ingest workers.
        """
        if not self._enabled:
            return
        ended = _now()
        num_embeddings = num_chunks if status == OBS_INGEST_COMPLETED else 0
        # Embedding cost = tokens actually embedded × the model's per-token input rate. Tokens are
        # only embedded when the batch upsert ran (completed), so cost is 0 for pre-embed failures.
        cost_usd = (
            compute_cost_usd(embedding_model or "", int(total_tokens or 0), output_tokens=0)
            if status == OBS_INGEST_COMPLETED else 0.0
        )
        self._execute(
            f"""
            INSERT INTO {_INGESTION}
                (ingestion_uid, run_id, index_key, document_type, document_source, source_file,
                 folder, scope_id, status, num_chunks, num_embeddings, total_tokens,
                 embedding_model, embedding_dimensions, cost_usd, error,
                 started_at, ended_at, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()), run_id, index_key, document_type, document_source, source_file,
                folder, scope_id, status, num_chunks, num_embeddings, total_tokens,
                embedding_model, embedding_dimensions, cost_usd, self._truncate(error),
                started_at, ended, _duration_ms(started_at, ended),
            ),
        )

    def record_llm_call(self, record: dict[str, Any]) -> None:
        if not self._enabled or not record.get("run_id"):
            return
        started = record.get("started_at")
        ended = record.get("ended_at")
        self._execute(
            f"""
            INSERT INTO {_LLM_CALL}
                (call_uid, run_id, question_uid, stage, agent_name, scope_area,
                 prompt_name, prompt_version, prompt_hash, model, temperature, attempt,
                 status, error, started_at, ended_at, latency_ms,
                 input_tokens, output_tokens, cached_tokens, reasoning_tokens, total_tokens,
                 cost_usd, request_payload, response_payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()), record.get("run_id"), record.get("question_uid"),
                record.get("stage"), record.get("agent_name"), record.get("scope_area"),
                record.get("prompt_name"), record.get("prompt_version"), record.get("prompt_hash"),
                record.get("model"), record.get("temperature"), record.get("attempt"),
                record.get("status"), record.get("error"), started, ended,
                _duration_ms(started, ended),
                record.get("input_tokens"), record.get("output_tokens"),
                record.get("cached_tokens"), record.get("reasoning_tokens"),
                record.get("total_tokens"), record.get("cost_usd"),
                self._truncate(record.get("request_payload")),
                self._truncate(record.get("response_payload")),
            ),
        )

    def record_finding(self, record: dict[str, Any]) -> None:
        if not self._enabled or not record.get("run_id"):
            return
        thresholds = record.get("thresholds_applied")
        self._execute(
            f"""
            INSERT INTO {_FINDING}
                (finding_uid, run_id, question_uid, origin, scope_area, risk_type,
                 risk_category, likelihood, impact, matrix_score, llm_confidence,
                 derived_confidence, thresholds_applied, is_recurrence, past_occurrence_ref)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()), record.get("run_id"), record.get("question_uid"),
                record.get("origin"), record.get("scope_area"), record.get("risk_type"),
                record.get("risk_category"), record.get("likelihood"), record.get("impact"),
                record.get("matrix_score"), record.get("llm_confidence"),
                record.get("derived_confidence"),
                json.dumps(thresholds) if thresholds is not None else None,
                record.get("is_recurrence"), record.get("past_occurrence_ref"),
            ),
        )

    def load_prior_scope_summary(
        self, auditor_id: str, exclude_run_id: str | None = None
    ) -> dict[str, dict[str, int]]:
        """Per-scope aggregated-finding category counts from this auditor's most recent COMPLETED prior
        run (origin='aggregated'). Returns {scope_area: {risk_category: count}}; {} when observability
        is disabled, no prior completed run exists, or the read fails — so callers no-op safely."""
        if not self._enabled:
            return {}
        exclude_clause = "AND run_id <> %s" if exclude_run_id else ""
        params: list[Any] = [OBS_ORIGIN_AGGREGATED, auditor_id, OBS_STATUS_COMPLETED]
        if exclude_run_id:
            params.append(exclude_run_id)
        rows = self._execute(
            f"""
            SELECT f.scope_area, f.risk_category, COUNT(*) AS cnt
            FROM {_FINDING} f
            WHERE f.origin = %s
              AND f.run_id = (
                  SELECT run_id FROM {_RUN}
                  WHERE auditor_id = %s AND status = %s {exclude_clause}
                  ORDER BY started_at DESC
                  LIMIT 1
              )
            GROUP BY f.scope_area, f.risk_category
            """,
            tuple(params),
            fetch=True,
        )
        summary: dict[str, dict[str, int]] = {}
        for row in rows or []:
            scope, category, cnt = row[0], row[1], int(row[2] or 0)
            if scope:
                summary.setdefault(scope, {})[category or ""] = cnt
        return summary

    def finish_run(self, *, run_id: str | None, status: str, started_at: datetime | None) -> None:
        if not self._enabled or not run_id:
            return
        ended = _now()
        self._execute(
            f"""
            UPDATE {_RUN} SET
                status = %s,
                ended_at = %s,
                duration_ms = %s,
                questions_completed = (SELECT count(*) FROM {_QUESTION} WHERE run_id = %s AND status = %s),
                questions_skipped   = (SELECT count(*) FROM {_QUESTION} WHERE run_id = %s AND status = %s),
                questions_error     = (SELECT count(*) FROM {_QUESTION} WHERE run_id = %s AND status = %s),
                findings_high     = (SELECT count(*) FROM {_FINDING} WHERE run_id = %s AND risk_category = %s),
                findings_medium   = (SELECT count(*) FROM {_FINDING} WHERE run_id = %s AND risk_category = %s),
                findings_low      = (SELECT count(*) FROM {_FINDING} WHERE run_id = %s AND risk_category = %s),
                findings_no_risk  = (SELECT count(*) FROM {_FINDING} WHERE run_id = %s AND risk_category = %s),
                total_llm_calls   = (SELECT count(*)                        FROM {_LLM_CALL} WHERE run_id = %s),
                total_input_tokens  = (SELECT COALESCE(sum(input_tokens), 0)  FROM {_LLM_CALL} WHERE run_id = %s),
                total_output_tokens = (SELECT COALESCE(sum(output_tokens), 0) FROM {_LLM_CALL} WHERE run_id = %s),
                total_tokens        = (SELECT COALESCE(sum(total_tokens), 0)  FROM {_LLM_CALL} WHERE run_id = %s),
                total_cached_tokens = (SELECT COALESCE(sum(cached_tokens), 0) FROM {_LLM_CALL} WHERE run_id = %s),
                total_reasoning_tokens = (SELECT COALESCE(sum(reasoning_tokens), 0) FROM {_LLM_CALL} WHERE run_id = %s),
                total_cost_usd      = (SELECT COALESCE(sum(cost_usd), 0)      FROM {_LLM_CALL} WHERE run_id = %s)
            WHERE run_id = %s
            """,
            (
                status, ended, _duration_ms(started_at, ended),
                run_id, OBS_QSTATUS_COMPLETED, run_id, OBS_QSTATUS_SKIPPED, run_id, OBS_QSTATUS_ERROR,
                run_id, RISK_CATEGORY_HIGH, run_id, RISK_CATEGORY_MEDIUM,
                run_id, RISK_CATEGORY_LOW, run_id, RISK_CATEGORY_NONE,
                run_id, run_id, run_id, run_id, run_id, run_id, run_id,
                run_id,
            ),
        )
        logger.info("[Obs] Run finished | run_id=%s | status=%s", run_id, status)

    def close(self) -> None:
        with self._lock:
            try:
                if self._conn is not None:
                    self._conn.close()
            except Exception:
                pass
            self._conn = None
