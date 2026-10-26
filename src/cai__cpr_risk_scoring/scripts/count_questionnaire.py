"""Count how many questions are in the IDR and Planning questionnaire tables for the configured auditor.

It reads the SAME tables the audit pipeline uses — settings.idr_table / settings.planning_table_name in
<catalog>.<schema> — via the same repository loader, so the counts match exactly what a run will
process. Set AUDITOR_ID (and, for a specific auditor, the table names via IDR_TABLE /
PLANNING_TABLE_NAME in .env or settings) before running.

Run (from the project root, venv active, .env configured):
    python scripts/count_questionnaire.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `src` importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from src.config.settings import get_settings  # noqa: E402
from src.repositories.questionnaire_repo import DatabricksQuestionnaireRepository  # noqa: E402


def _raw_count(repo: DatabricksQuestionnaireRepository, fqn: str) -> int:
    """Total rows in the table (before the loader's blank-Q/R filtering).

    Uses fetchall() — the same result path the repo's load_all uses successfully. The Databricks
    SQLAlchemy dialect raises on .scalar() for a COUNT(*) result, so we count the fetched rows instead.
    """
    with repo._engine.connect() as conn:  # reuse the repo's warehouse engine
        rows = conn.execute(text(f"SELECT * FROM {fqn}")).fetchall()
    return len(rows)


def main() -> int:
    settings = get_settings()
    repo = DatabricksQuestionnaireRepository(settings)
    catalog, schema = settings.databricks_catalog, settings.databricks_schema_name
    tables = {"idr": settings.idr_table, "planning": settings.planning_table_name}

    print(f"Auditor: {settings.auditor_id}")
    print(f"  {'type':9} | {'table':52} | {'rows':>6} | {'usable':>6} | {'skipped':>7}")
    tot_raw = tot_use = 0
    for qtype, table in tables.items():
        fqn = f"{catalog}.{schema}.{table}"
        try:
            # usable = rows with a question OR a response (what the pipeline processes);
            # skipped = truly-empty rows (no question AND no response).
            usable = len(repo.load_all(
                scope_id=settings.current_scope_id,
                auditor_id=settings.auditor_id,
                questionnaire_type=qtype,
            ))
            raw = _raw_count(repo, fqn)
            tot_raw += raw
            tot_use += usable
            print(f"  {qtype:9} | {fqn:52} | {raw:6} | {usable:6} | {raw - usable:7}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {qtype:9} | {fqn:52} | ERROR: {exc}")
    print(f"  {'TOTAL':9} | {'':52} | {tot_raw:6} | {tot_use:6} | {tot_raw - tot_use:7}")
    print("\n  rows    = all rows in the table")
    print("  usable  = rows with a question OR a response (what the audit processes)")
    print("  skipped = truly-empty rows (no question AND no response)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
    
