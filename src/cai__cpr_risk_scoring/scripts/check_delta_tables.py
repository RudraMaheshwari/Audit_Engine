"""
check_delta_tables.py — Quick row-count check for the input questionnaire Delta tables.

Usage:
    python scripts/check_delta_tables.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from databricks import sql as dbsql
from src.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    catalog = settings.databricks_catalog
    schema = settings.databricks_schema_name

    tables = {
        settings.idr_table:            "IDR questionnaire",
        settings.planning_table_name:  "Planning questionnaire",
    }

    host = settings.databricks_host.replace("https://", "").rstrip("/")
    conn = dbsql.connect(
        server_hostname=host,
        http_path=f"/sql/1.0/warehouses/{settings.databricks_warehouse_id}",
        access_token=settings.databricks_token,
    )
    cursor = conn.cursor()

    print(f"\nInput tables in `{catalog}`.`{schema}`\n")
    print(f"  {'Table':<40} {'Rows':>8}  Description")
    print(f"  {'─'*40} {'─'*8}  {'─'*25}")

    for table, desc in tables.items():
        fq = f"`{catalog}`.`{schema}`.`{table}`"
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {fq}")
            count = cursor.fetchone()[0]
            print(f"  {table:<40} {count:>8}  {desc}")
        except Exception:
            print(f"  {table:<40} {'N/A':>8}  {desc} (not found)")

    cursor.close()
    conn.close()
    print()


if __name__ == "__main__":
    main()
