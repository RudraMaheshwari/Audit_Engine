"""
Standalone verifier: is the "HCC monitoring reviewed_count=0 / validated_count=0" signal REAL,
or a query/hallucination artifact?

It is read-only. It does NOT import or run any pipeline code. It:
  1. Connects to the Databricks SQL warehouse using the project's Settings (.env).
  2. DESCRIBEs all 7 GLD dashboard tables and searches every column name for
     'review' / 'validat' / 'monitor' / 'count' — the fields the findings claim to read.
  3. Dumps the HCC dashboard schema + row count + a 3-row sample so we can see what it
     actually contains (FFS/event data vs a monitoring-review log).
  4. Prints a VERDICT: if no review/validation columns exist anywhere, the "zero monitoring"
     finding is built on non-existent columns -> phantom signal.

Run:  python test_monitoring_query.py
"""

from databricks import sql

from src.config.constants import DATABRICKS_WAREHOUSE_PATH_PREFIX
from src.config.settings import Settings

KEYWORDS = ("review", "validat", "monitor", "count")


def _connect(s: Settings):
    host = s.databricks_host.replace("https://", "").rstrip("/")
    http_path = f"{DATABRICKS_WAREHOUSE_PATH_PREFIX}{s.databricks_warehouse_id}"
    print(f"Connecting: host={host} | http_path={http_path}")
    return sql.connect(server_hostname=host, http_path=http_path, access_token=s.databricks_token)


def _fqn(s: Settings, table: str) -> str:
    return f"`{s.databricks_catalog}`.`{s.databricks_transactional_schema}`.`{table}`"


def _describe(cur, fqn: str) -> list[str]:
    cur.execute(f"DESCRIBE TABLE {fqn}")
    cols = []
    for row in cur.fetchall():
        name = (row[0] or "").strip()
        if not name or name.startswith("#"):
            break
        cols.append(name)
    return cols


def main() -> None:
    s = Settings()  # loads .env
    conn = _connect(s)
    cur = conn.cursor()

    tables = s.dashboard_table_names  # {key: table_name}
    hits_by_table: dict[str, list[str]] = {}

    print("\n" + "=" * 90)
    print("STEP 1 — scan ALL 7 dashboards for review/validation/monitoring/count columns")
    print("=" * 90)
    for key, table in tables.items():
        fqn = _fqn(s, table)
        try:
            cols = _describe(cur, fqn)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{key:14}] {table}  ->  ERROR: {exc}")
            continue
        hits = [c for c in cols if any(k in c.lower() for k in KEYWORDS)]
        hits_by_table[table] = hits
        print(f"  [{key:14}] {table}  |  {len(cols)} cols  |  keyword hits: {hits or 'NONE'}")

    # ── STEP 2 — the columns DO exist; are they actually POPULATED? ──────────────────────
    # This is the real test: if HCCO_REVIEW_DATE / VALIDATION_RESULT / VALIDATION_DATE are
    # populated on real rows, the "zero monitoring" finding is FALSE. If they are ~all NULL,
    # the finding is legitimate. Everything is CAST to STRING to dodge the connector's
    # arrow->numpy null-coercion bug that crashed the previous COUNT(*).
    review_targets = [
        "HCCO_REVIEW_DATE",
        "VALIDATION_RESULT",
        "VALIDATION_DATE",
        "GL_ACCOUNT_VALIDATION_FLAG",
        "APPROVAL_DATE",
        "APPROVAL_BEFORE_THE_START_DATE",
        "EVENT_REVIEWER_WWID",
        "STAGE_STATUS",
    ]
    check_tables = {
        "hcc": s.hcc_dashboard_table,
        "disbursement": s.disbursement_table,
        "payment": s.payment_dashboard_table,
    }

    print("\n" + "=" * 90)
    print("STEP 2 — are the review/validation columns actually POPULATED? (populated / total rows)")
    print("=" * 90)
    populated_summary: dict[str, dict[str, tuple[int, int]]] = {}
    for key, table in check_tables.items():
        fqn = _fqn(s, table)
        cols = set(_describe(cur, fqn))
        present = [c for c in review_targets if c in cols]
        if not present:
            print(f"\n[{key}] {table}: none of the target columns present")
            continue
        # COUNT(col) ignores NULLs, so it = number of populated (non-null) rows.
        parts = ["CAST(COUNT(*) AS STRING) AS total"] + [
            f"CAST(COUNT(`{c}`) AS STRING) AS `{c}`" for c in present
        ]
        cur.execute(f"SELECT {', '.join(parts)} FROM {fqn}")
        row = cur.fetchone()
        total = int(row[0])
        print(f"\n[{key}] {table}  |  total rows = {total}")
        populated_summary[key] = {}
        for i, c in enumerate(present, start=1):
            pop = int(row[i])
            populated_summary[key][c] = (pop, total)
            pct = (pop / total * 100) if total else 0.0
            print(f"     {c:32} populated: {pop:>8} / {total:<8} ({pct:5.1f}%)")

    # Distinct VALIDATION_RESULT values (if present) — shows WHAT the validations say.
    hcc_fqn = _fqn(s, s.hcc_dashboard_table)
    hcc_cols = set(_describe(cur, hcc_fqn))
    if "VALIDATION_RESULT" in hcc_cols:
        print("\nDistinct VALIDATION_RESULT values in HCC table:")
        cur.execute(
            f"SELECT CAST(VALIDATION_RESULT AS STRING) AS v, CAST(COUNT(*) AS STRING) AS n "
            f"FROM {hcc_fqn} GROUP BY VALIDATION_RESULT ORDER BY COUNT(*) DESC LIMIT 20"
        )
        for r in cur.fetchall():
            print(f"     {str(r[0])!r:40} -> {r[1]}")

    print("\n" + "=" * 90)
    print("VERDICT")
    print("=" * 90)
    hcc_pop = populated_summary.get("hcc", {})
    key_cols = ("HCCO_REVIEW_DATE", "VALIDATION_RESULT", "VALIDATION_DATE")
    any_populated = any(hcc_pop.get(c, (0, 0))[0] > 0 for c in key_cols)
    if any_populated:
        print("  Review/validation columns ARE populated on real rows (see % above).")
        print("  => 'reviewed_count=0 / validated_count=0' is likely an AGGREGATION ARTIFACT")
        print("     (agent counted the wrong field/basis). Family A (~28 rows) is FALSE.")
    else:
        print("  Review/validation columns are ~entirely NULL.")
        print("  => 'no HCC review/validation recorded' is a LEGITIMATE finding — but it is ONE")
        print("     finding, not 28. It should be raised once on its owner question, not per row.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
