"""Idempotent DDL for the observability tables.

The DDL uses CREATE TABLE / INDEX IF NOT EXISTS, so it is safe to run on every startup: it creates
the five tables on the first run and is a no-op on every subsequent run. Table and schema names
come from constants so there is a single source of truth. obs_ingestion.run_id is intentionally a
plain nullable column (no FK), so an ingestion row is never rejected if it is written before/around
the obs_run row or while Lakebase was briefly unavailable.
"""

from src.config.constants import (
    OBS_SCHEMA,
    OBS_TABLE_FINDING,
    OBS_TABLE_INGESTION,
    OBS_TABLE_LLM_CALL,
    OBS_TABLE_QUESTION,
    OBS_TABLE_RUN,
)

_RUN = f"{OBS_SCHEMA}.{OBS_TABLE_RUN}"
_QUESTION = f"{OBS_SCHEMA}.{OBS_TABLE_QUESTION}"
_LLM_CALL = f"{OBS_SCHEMA}.{OBS_TABLE_LLM_CALL}"
_FINDING = f"{OBS_SCHEMA}.{OBS_TABLE_FINDING}"
_INGESTION = f"{OBS_SCHEMA}.{OBS_TABLE_INGESTION}"


SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {_RUN} (
        run_id                UUID PRIMARY KEY,
        auditor_id            TEXT NOT NULL,
        status                TEXT NOT NULL,
        started_at            TIMESTAMPTZ NOT NULL,
        ended_at              TIMESTAMPTZ,
        duration_ms           BIGINT,
        model                 TEXT,
        prompt_bundle_id      TEXT,
        prompt_versions       JSONB,
        run_config            JSONB,
        app_version           TEXT,
        total_questions       INT,
        questions_completed   INT,
        questions_skipped     INT,
        questions_error       INT,
        findings_high         INT,
        findings_medium       INT,
        findings_low          INT,
        findings_no_risk      INT,
        total_llm_calls       INT,
        total_input_tokens    BIGINT,
        total_output_tokens   BIGINT,
        total_tokens          BIGINT,
        total_cached_tokens   BIGINT,
        total_reasoning_tokens BIGINT,
        total_cost_usd        NUMERIC(14, 6),
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_QUESTION} (
        question_uid          UUID PRIMARY KEY,
        run_id                UUID NOT NULL REFERENCES {_RUN}(run_id),
        question_index        INT,
        source_file           TEXT,
        question_hash         TEXT,
        assigned_scope        TEXT,
        final_risk_category   TEXT,
        final_matrix_score    INT,
        likelihood            INT,
        impact                INT,
        llm_confidence        NUMERIC(4, 3),
        derived_confidence    NUMERIC(4, 3),
        transactional_invoked BOOLEAN,
        status                TEXT,
        error                 TEXT,
        started_at            TIMESTAMPTZ,
        ended_at              TIMESTAMPTZ,
        duration_ms           BIGINT,
        num_llm_calls         INT,
        input_tokens          BIGINT,
        output_tokens         BIGINT,
        total_tokens          BIGINT,
        cost_usd              NUMERIC(14, 6),
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_LLM_CALL} (
        call_uid              UUID PRIMARY KEY,
        run_id                UUID NOT NULL REFERENCES {_RUN}(run_id),
        question_uid          UUID REFERENCES {_QUESTION}(question_uid),
        stage                 TEXT,
        agent_name            TEXT,
        scope_area            TEXT,
        prompt_name           TEXT,
        prompt_version        TEXT,
        prompt_hash           TEXT,
        model                 TEXT,
        temperature           NUMERIC,
        attempt               INT,
        status                TEXT,
        error                 TEXT,
        started_at            TIMESTAMPTZ,
        ended_at              TIMESTAMPTZ,
        latency_ms            BIGINT,
        input_tokens          BIGINT,
        output_tokens         BIGINT,
        cached_tokens         BIGINT,
        reasoning_tokens      BIGINT,
        total_tokens          BIGINT,
        cost_usd              NUMERIC(14, 6),
        request_payload       TEXT,
        response_payload      TEXT,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_FINDING} (
        finding_uid           UUID PRIMARY KEY,
        run_id                UUID NOT NULL REFERENCES {_RUN}(run_id),
        question_uid          UUID REFERENCES {_QUESTION}(question_uid),
        origin                TEXT,
        scope_area            TEXT,
        risk_type             TEXT,
        risk_category         TEXT,
        likelihood            INT,
        impact                INT,
        matrix_score          INT,
        llm_confidence        NUMERIC(4, 3),
        derived_confidence    NUMERIC(4, 3),
        thresholds_applied    JSONB,
        is_recurrence         BOOLEAN,
        past_occurrence_ref   TEXT,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {_INGESTION} (
        ingestion_uid         UUID PRIMARY KEY,
        run_id                UUID,
        index_key             TEXT,
        document_type         TEXT,
        document_source       TEXT,
        source_file           TEXT,
        folder                TEXT,
        scope_id              TEXT,
        status                TEXT,
        num_chunks            INT,
        num_embeddings        INT,
        total_tokens          BIGINT,
        embedding_model       TEXT,
        embedding_dimensions  INT,
        cost_usd              NUMERIC(14, 6),
        error                 TEXT,
        started_at            TIMESTAMPTZ,
        ended_at              TIMESTAMPTZ,
        duration_ms           BIGINT,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


# Best-effort migrations + indexes for PRE-EXISTING tables (CREATE TABLE IF NOT EXISTS above won't add
# new columns to a table that already exists). These require table OWNERSHIP in Postgres (ALTER /
# CREATE INDEX), so when the connecting role is NOT the table owner they fail with
# "must be owner of table …". That is harmless: the new columns already exist in the CREATE TABLE DDL
# above for any table this app created, so the recorder runs these QUIETLY, never retries the
# permission error, and the audit run is unaffected. To actually apply them, run them once as the
# table owner (or make the app's Service Principal the owner) — see the ops note in the recorder.
MIGRATION_STATEMENTS: tuple[str, ...] = (
    f"ALTER TABLE {_RUN} ADD COLUMN IF NOT EXISTS total_cached_tokens BIGINT",
    f"ALTER TABLE {_RUN} ADD COLUMN IF NOT EXISTS total_reasoning_tokens BIGINT",
    f"CREATE INDEX IF NOT EXISTS ix_obs_question_run ON {_QUESTION}(run_id)",
    f"CREATE INDEX IF NOT EXISTS ix_obs_llmcall_run ON {_LLM_CALL}(run_id)",
    f"CREATE INDEX IF NOT EXISTS ix_obs_llmcall_question ON {_LLM_CALL}(question_uid)",
    f"CREATE INDEX IF NOT EXISTS ix_obs_finding_run ON {_FINDING}(run_id)",
    f"CREATE INDEX IF NOT EXISTS ix_obs_ingestion_run ON {_INGESTION}(run_id)",
)
