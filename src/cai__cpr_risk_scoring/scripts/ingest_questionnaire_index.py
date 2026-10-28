"""Ingest questionnaire Q&A into the SHARED questionnaire index, for ANY auditor.

Two sources are supported (pick ONE per run):

  VOLUME mode  — read Q&A files from a Databricks Volume path (.parquet/.json/.csv/.xlsx):
      python scripts/ingest_questionnaire_index.py --auditor Naeem_7 \
          --path /Volumes/nbod_gaagd/cpr_risk_analysis/questionnaire_data

  TABLE mode   — read Q&A from one or TWO Databricks tables (a planning table and/or an IDR table):
      # two explicit tables
      python scripts/ingest_questionnaire_index.py --auditor Naeem_7 \
          --planning-table consolidated_naeem_7 --idr-table idr_naeem_7
      # just one
      python scripts/ingest_questionnaire_index.py --auditor Naeem_7 --idr-table idr_naeem_7
      # the two tables already configured in Settings (planning_table_name + idr_table)
      python scripts/ingest_questionnaire_index.py --auditor Naeem_7 --from-tables

Table names may be bare (catalog.schema is added from Settings) or fully qualified. The two
questionnaire types differ only in default column mapping (planning vs idr); an LLM column-mapper
handles non-standard headers.

Both modes use the SAME record shape and chunk_id scheme Risk Analysis uses, so rows are reused /
overwritten by primary key — never duplicated. Flags:
    --scope <id>    scope_id to tag rows (default: Settings.current_scope_id)
    --force         re-ingest even if the index already holds rows for this auditor
    --dry-run       load + report the record count; embed/upsert nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src` importable and load the project-root .env when run directly from anywhere (e.g. scripts/).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))

from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider  # noqa: E402
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider  # noqa: E402
from src.pipeline.runner import (  # noqa: E402  (reuse RA's record builder + volume loader)
    _build_questionnaire_records,
    _load_questionnaire_from_volume,
)
from src.repositories.questionnaire_repo import DatabricksQuestionnaireRepository  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402
from src.vector_store.index_manager import VectorIndexManager  # noqa: E402

logger = get_logger("ingest_questionnaire_index")


def _load_from_tables(settings, auditor_id: str, scope_id: str,
                      planning_table: str | None, idr_table: str | None) -> list[dict]:
    """Load Q&A from one or two Databricks tables and build index records (RA's chunk_id scheme)."""
    try:
        llm = AzureOpenAILLMProvider(settings).get_client()  # optional column-mapper for odd headers
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IngestQuestionnaire] LLM column-mapper unavailable — using header/defaults | %s", exc)
        llm = None

    repo = DatabricksQuestionnaireRepository(settings, llm=llm)
    pairs: list[tuple[str, str]] = []
    if planning_table:
        pairs.append(("planning", planning_table))
    if idr_table:
        pairs.append(("idr", idr_table))

    questions = []
    for qtype, table in pairs:
        try:
            qs = repo.load_all(scope_id=scope_id, auditor_id=auditor_id,
                               questionnaire_type=qtype, table=table)
            logger.info("[IngestQuestionnaire] Loaded %d from %s table | table=%s", len(qs), qtype, table)
            questions.extend(qs)
        except Exception as exc:  # noqa: BLE001
            logger.error("[IngestQuestionnaire] Failed to load %s table %s | %s", qtype, table, exc)
    return _build_questionnaire_records(questions)


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest questionnaire Q&A (volume OR table source) into the questionnaire index.")
    p.add_argument("--auditor", "--auditor-id", dest="auditor", required=True,
                   help="auditor_id to tag the ingested rows (e.g. Naeem_7)")
    # Source: volume ...
    p.add_argument("--path", dest="path", default=None,
                   help="VOLUME mode: questionnaire volume path (parent OR the auditor's folder).")
    # ... or tables
    p.add_argument("--planning-table", dest="planning_table", default=None,
                   help="TABLE mode: planning questionnaire table (bare or catalog.schema.table).")
    p.add_argument("--idr-table", dest="idr_table", default=None,
                   help="TABLE mode: IDR questionnaire table (bare or catalog.schema.table).")
    p.add_argument("--from-tables", action="store_true",
                   help="TABLE mode: use the two tables configured in Settings "
                        "(planning_table_name + idr_table).")
    p.add_argument("--scope", dest="scope", default=None,
                   help="scope_id to tag rows (default: Settings.current_scope_id)")
    p.add_argument("--force", action="store_true",
                   help="re-ingest even if the index already holds rows for this auditor")
    p.add_argument("--dry-run", action="store_true",
                   help="load + report the record count; embed/upsert nothing")
    args = p.parse_args()

    auditor_id = (args.auditor or "").strip()
    if not auditor_id:
        logger.error("[IngestQuestionnaire] --auditor is required")
        return 2

    use_tables = bool(args.planning_table or args.idr_table or args.from_tables)
    if use_tables and args.path:
        logger.error("[IngestQuestionnaire] Choose ONE source: --path (volume) OR table flags — not both.")
        return 2

    base_settings = get_settings()
    scope_id = (args.scope or base_settings.current_scope_id or "").strip()

    # Resolve table names for table mode; default to the configured pair when --from-tables.
    planning_table = args.planning_table
    idr_table = args.idr_table
    if args.from_tables:
        planning_table = planning_table or (base_settings.planning_table_name or None)
        idr_table = idr_table or (base_settings.idr_table or None)

    # Volume mode: override the volume path for this run only (fresh copy; cached Settings untouched).
    settings = base_settings
    path = None
    if not use_tables:
        path = (args.path or getattr(base_settings, "questionnaire_volume_path", "") or "").strip()
        if not path:
            logger.error("[IngestQuestionnaire] No source given. Use --path (volume) or "
                         "--planning-table/--idr-table/--from-tables (tables).")
            return 2
        settings = base_settings.model_copy(update={"questionnaire_volume_path": path})

    try:
        index_manager = VectorIndexManager(settings, AzureOpenAIEmbeddingProvider(settings))
    except Exception as exc:  # noqa: BLE001
        logger.error("[IngestQuestionnaire] Could not initialise the index manager | %s", exc)
        return 1

    src_desc = (f"tables(planning={planning_table}, idr={idr_table})" if use_tables else f"volume({path})")
    logger.info("[IngestQuestionnaire] Start | auditor_id=%s | scope=%s | source=%s",
                auditor_id, scope_id, src_desc)

    if not args.force and not args.dry_run and index_manager.is_questionnaire_populated(auditor_id):
        logger.info("[IngestQuestionnaire] Index already populated for auditor=%s — skipping "
                    "(pass --force to re-ingest).", auditor_id)
        return 0

    try:
        if use_tables:
            records = _load_from_tables(settings, auditor_id, scope_id, planning_table, idr_table)
        else:
            records = _load_questionnaire_from_volume(settings, auditor_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[IngestQuestionnaire] Load failed | auditor_id=%s | source=%s | %s",
                     auditor_id, src_desc, exc)
        return 1

    logger.info("[IngestQuestionnaire] Loaded %d record(s) | auditor_id=%s", len(records), auditor_id)
    if not records:
        logger.warning("[IngestQuestionnaire] Nothing to ingest — no readable Q&A from the source.")
        return 0

    if args.dry_run:
        sample = records[0]
        logger.info("[IngestQuestionnaire] DRY-RUN — nothing upserted | sample chunk_id=%s | q=%.80s",
                    sample.get("chunk_id"), (sample.get("question_text") or ""))
        return 0

    try:
        index_manager.ensure_questionnaire_index()
        index_manager.upsert_questionnaire(records)
    except Exception as exc:  # noqa: BLE001
        logger.error("[IngestQuestionnaire] Upsert failed | auditor_id=%s | %s", auditor_id, exc)
        return 1

    logger.info("[IngestQuestionnaire] DONE | upserted %d record(s) | auditor_id=%s | index=%s",
                len(records), auditor_id, index_manager._index_names["questionnaire"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
