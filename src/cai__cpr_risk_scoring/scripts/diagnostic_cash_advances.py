"""One-off diagnostic: does cash-advance / petty-cash / manual-check data exist for this audit,
and under which column/identifier? Scoped to the audit's ENGAGEMENT_NUMBER(s).

Reads connection details, table names, AND the engagement filter from settings/.env
(AUDIT_ENGAGEMENT_NUMBERS) — nothing hardcoded.

Run from the project root (same env you run the pipeline in):
    python diagnostic_cash_advances.py

It prints, per step, a copy-pasteable block. Paste the whole output back so I can write the
CASH_ADVANCES scope's Data Calculations against the REAL columns/values (no guessing).

Read-only: only SELECT / DESCRIBE — writes nothing.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from src.config.constants import DATABRICKS_WAREHOUSE_PATH_PREFIX
from src.config.settings import get_settings


def build_engine(s):
    host = s.databricks_host.replace("https://", "")
    uri = (
        f"databricks://token:{s.databricks_token}@{host}"
        f"?http_path={DATABRICKS_WAREHOUSE_PATH_PREFIX}{s.databricks_warehouse_id}"
        f"&catalog={s.databricks_catalog}"
        f"&schema={s.databricks_schema_name}"
    )
    return create_engine(uri, pool_pre_ping=True)


def run(engine, label: str, sql: str) -> None:
    print("\n" + "=" * 78)
    print(f"### {label}")
    print("-" * 78)
    print(sql.strip())
    print("-" * 78)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            rows = result.fetchall()
        if not rows:
            print("(no rows returned)")
            return
        print(" | ".join(cols))
        for r in rows:
            print(" | ".join("" if v is None else str(v) for v in r))
        print(f"({len(rows)} row(s))")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")


def main() -> None:
    s = get_settings()
    eng = build_engine(s)

    eng_filter = s.engagement_filter_clause  # from AUDIT_ENGAGEMENT_NUMBERS in .env — never hardcoded
    print(f"Engagement filter in use: {eng_filter}")

    te = f"{s.databricks_catalog}.{s.databricks_transactional_schema}.{s.travel_expense_dashboard_table}"
    disb = f"{s.databricks_catalog}.{s.databricks_transactional_schema}.{s.disbursement_table}"

    # 0) Confirm the real column names first (in case the dictionary names differ from your schema).
    run(eng, "T&E table columns", f"DESCRIBE {te}")
    run(eng, "Disbursement table columns", f"DESCRIBE {disb}")

    # 1) T&E: distinct expense types for this audit (eyeball for advance/petty-cash rows).
    run(
        eng,
        "T&E distinct expense types (this audit)",
        f"""
        SELECT `RPT_ENT_EXP_TYPE_NM`, COUNT(*) AS n
        FROM {te}
        WHERE {eng_filter}
        GROUP BY `RPT_ENT_EXP_TYPE_NM`
        ORDER BY n DESC
        LIMIT 50
        """,
    )

    # 2) Keyword scan across T&E + AP for advance / petty cash / imprest / manual.
    run(
        eng,
        "Keyword scan T&E EXPENSE_NAME + AP DOC_DESCRIPTION (this audit)",
        f"""
        SELECT 'TE' AS src, `EXPENSE_NAME` AS label, COUNT(*) AS n
        FROM {te}
        WHERE {eng_filter}
          AND LOWER(`EXPENSE_NAME`) RLIKE 'advance|petty|imprest|float'
        GROUP BY `EXPENSE_NAME`
        UNION ALL
        SELECT 'AP' AS src, `DOC_DESCRIPTION` AS label, COUNT(*) AS n
        FROM {disb}
        WHERE {eng_filter}
          AND LOWER(`DOC_DESCRIPTION`) RLIKE 'advance|petty|imprest|manual'
        GROUP BY `DOC_DESCRIPTION`
        ORDER BY n DESC
        LIMIT 50
        """,
    )

    # 3) AP disbursement: distinct DOC_TYPE / DOC_DESCRIPTION for this audit (context).
    run(
        eng,
        "AP disbursement distinct DOC_TYPE / DOC_DESCRIPTION (this audit)",
        f"""
        SELECT `DOC_TYPE`, `DOC_DESCRIPTION`, COUNT(*) AS n
        FROM {disb}
        WHERE {eng_filter}
        GROUP BY `DOC_TYPE`, `DOC_DESCRIPTION`
        ORDER BY n DESC
        LIMIT 50
        """,
    )

    print("\nDone. Copy everything above and paste it back.")


if __name__ == "__main__":
    main()
