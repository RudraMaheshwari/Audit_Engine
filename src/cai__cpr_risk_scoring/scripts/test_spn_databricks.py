"""Verify the Databricks Service Principal (OAuth M2M) has every access the FCPA Audit app needs —
BEFORE swapping PAT auth in the code.

Checks (each isolated — one failure does not stop the others; a pass/fail matrix prints at the end):
  1. Auth (OAuth M2M)            — SP credentials are valid
  2. SQL warehouse + UC read     — SELECT + SHOW TABLES on the catalog/schema
  3. Table write/read/delete     — CREATE → INSERT → SELECT → DROP a scratch table
  4. Volume read                 — list a volume directory
  5. Volume write/read/delete    — upload → download → delete a scratch file in a volume

Setup:
  pip install databricks-sdk databricks-sql-connector python-dotenv
  # .env (add these; DATABRICKS_TOKEN may stay — it is ignored here):
  #   DATABRICKS_HOST=adb-xxxx.azuredatabricks.net
  #   DATABRICKS_CLIENT_ID=<client_id>
  #   DATABRICKS_CLIENT_SECRET=<client_secret>
  #   DATABRICKS_WAREHOUSE_ID=<warehouse id>
  #   DATABRICKS_CATALOG=nbod_gaagd
  #   DATABRICKS_SCHEMA_NAME=<schema>
  #   TEST_VOLUME_PATH=/Volumes/nbod_gaagd/cpr_risk_analysis/auditors_supporting_docs   # a WRITABLE volume
Run:
  python test_spn_databricks.py
"""

import io
import os
import uuid

from dotenv import load_dotenv

load_dotenv()
# Force OAuth M2M: hide the PAT from the SDK so it does not see two auth methods (this edits only this
# process's env, not your .env file).
os.environ.pop("DATABRICKS_TOKEN", None)

from databricks import sql                                  # noqa: E402
from databricks.sdk import WorkspaceClient                  # noqa: E402
from databricks.sdk.core import Config, oauth_service_principal  # noqa: E402
from databricks.sdk.service.sql import StatementState       # noqa: E402

HOST   = os.environ["DATABRICKS_HOST"].replace("https://", "").rstrip("/")
CID    = os.environ["DATABRICKS_CLIENT_ID"]
SECRET = os.environ["DATABRICKS_CLIENT_SECRET"]
WH     = os.environ["DATABRICKS_WAREHOUSE_ID"]
CAT    = os.environ.get("DATABRICKS_CATALOG", "")
SCH    = os.environ.get("DATABRICKS_SCHEMA_NAME", "")

# Volumes to read/write-test. Override via TEST_VOLUME_PATHS (comma-separated) or use these defaults.
_DEFAULT_VOLS = [
    "/Volumes/nbod_gaagd/cpr_risk_analysis/test",
    "/Volumes/nbod_gaagd/cpr_risk_analysis/si-history",
]
VOLS = [v.strip().rstrip("/") for v in os.environ.get("TEST_VOLUME_PATHS", "").split(",") if v.strip()] \
    or _DEFAULT_VOLS

cfg = Config(host=f"https://{HOST}", client_id=CID, client_secret=SECRET, auth_type="oauth-m2m")
w = WorkspaceClient(config=cfg)

results: list[tuple[str, bool, str]] = []


def check(name):
    """Decorator-ish helper: run a function, record pass/fail + detail."""
    def run(fn):
        try:
            detail = fn() or "OK"
            results.append((name, True, str(detail)))
            print(f"  [PASS] {name}: {detail}")
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, str(exc)))
            print(f"  [FAIL] {name}: {exc}")
    return run


def _sql_conn():
    return sql.connect(
        server_hostname=HOST,
        http_path=f"/sql/1.0/warehouses/{WH}",
        credentials_provider=lambda: oauth_service_principal(cfg),
    )


def run_stmt(statement):
    """Run SQL via the SDK Statement Execution API (REST) — avoids the sql-connector's None-rowcount
    bug on DDL/DML. Returns the rows (list of lists) for a SELECT, else None. Raises on failure."""
    r = w.statement_execution.execute_statement(
        statement=statement, warehouse_id=WH, wait_timeout="30s",
    )
    if r.status.state != StatementState.SUCCEEDED:
        err = getattr(r.status, "error", None)
        raise RuntimeError(f"{r.status.state}: {getattr(err, 'message', err)}")
    return r.result.data_array if (r.result and r.result.data_array) else None


print("Databricks Service Principal access test\n" + "=" * 45)

# 1) Auth ---------------------------------------------------------------------
@check("1. Auth (OAuth M2M)")
def _():
    me = w.current_user.me()
    return f"authenticated as {me.user_name or me.display_name}"

# 2) SQL warehouse + Unity Catalog READ --------------------------------------
@check("2. Warehouse + UC read")
def _():
    with _sql_conn() as c, c.cursor() as cur:
        cur.execute("SELECT current_user(), current_catalog(), current_schema()")
        who = cur.fetchone()
        n = ""
        if CAT and SCH:
            cur.execute(f"SHOW TABLES IN {CAT}.{SCH}")
            n = f"; {len(cur.fetchall())} tables visible in {CAT}.{SCH}"
        return f"{who}{n}"

# 3) Table WRITE / READ / DELETE ---------------------------------------------
@check("3. Table create/insert/select/drop")
def _():
    if not (CAT and SCH):
        return "skipped (set DATABRICKS_CATALOG + DATABRICKS_SCHEMA_NAME)"
    tbl = f"{CAT}.{SCH}._spn_access_test_{uuid.uuid4().hex[:8]}"
    run_stmt(f"CREATE TABLE {tbl} (id INT, name STRING)")
    run_stmt(f"INSERT INTO {tbl} VALUES (1, 'test')")
    rows = run_stmt(f"SELECT COUNT(*) FROM {tbl}")
    cnt = rows[0][0] if rows else "?"
    run_stmt(f"DROP TABLE {tbl}")
    return f"created+inserted+read ({cnt} row) + dropped {tbl}"

# 4..N) Volume READ + WRITE/DELETE per configured volume ----------------------
def _make_vol_read(vol):
    def fn():
        entries = list(w.files.list_directory_contents(vol))
        return f"{len(entries)} entries under {vol}"
    return fn

def _make_vol_write(vol):
    def fn():
        fpath = f"{vol}/_spn_access_test_{uuid.uuid4().hex[:8]}.txt"
        payload = b"spn access test"
        w.files.upload(fpath, io.BytesIO(payload), overwrite=True)
        got = w.files.download(fpath).contents.read()
        assert got == payload, "downloaded content mismatch"
        w.files.delete(fpath)
        return f"uploaded+downloaded+deleted {fpath}"
    return fn

_idx = 4
for _vol in VOLS:
    check(f"{_idx}. Volume read  [{_vol}]")(_make_vol_read(_vol))
    _idx += 1
    check(f"{_idx}. Volume write/delete [{_vol}]")(_make_vol_write(_vol))
    _idx += 1

# Summary ---------------------------------------------------------------------
print("\n" + "=" * 45)
ok = sum(1 for _, p, _ in results if p)
for name, passed, detail in results:
    print(f"  {'OK ' if passed else 'XX '} {name}")
print(f"\n{ok}/{len(results)} checks passed.")
if ok == len(results):
    print("All access verified — safe to swap PAT -> SP OAuth in the code.")
else:
    print("Some checks failed — share the FAIL lines with the admin to grant the missing access.")
