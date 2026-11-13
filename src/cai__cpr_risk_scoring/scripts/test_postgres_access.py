"""Standalone Lakebase Postgres connectivity test.

Verifies that your Databricks token can reach the Lakebase Postgres instance by:
  1. resolving a connection (either a Lakebase instance via the Databricks SDK, or
     plain PG* env vars for any Postgres),
  2. connecting and printing the server version,
  3. CREATE TABLE  -> INSERT a row -> SELECT it back,
  4. reporting success (the test table is left in place so you can inspect it; a
     one-liner to drop it is printed at the end).

It writes NOTHING to the audit pipeline — it only touches a throwaway table
`public.pg_access_test`.

--------------------------------------------------------------------------------
PREREQUISITE (one-time):
    pip install "psycopg[binary]"

RUN — two ways:

  A) Lakebase-native (recommended). The SDK reads DATABRICKS_HOST + DATABRICKS_TOKEN
     from your environment / .env and mints a short-lived DB credential for you:

        set LAKEBASE_INSTANCE_NAME=<your-lakebase-instance-name>
        python test_postgres_access.py

     Optional overrides: PGDATABASE (default "databricks_postgres"),
     PGUSER (default = your Databricks username), PGPORT (default 5432).

  B) Direct Postgres (any host, if you already know the raw credentials):

        set PGHOST=<host>
        set PGPORT=5432
        set PGDATABASE=<db>
        set PGUSER=<user>
        set PGPASSWORD=<password-or-token>
        python test_postgres_access.py
--------------------------------------------------------------------------------
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

_TEST_TABLE = "public.pg_access_test"


def _load_dotenv_if_present() -> None:
    """Best-effort: load a local .env so DATABRICKS_HOST/TOKEN are available."""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        pass


def _require_psycopg():
    try:
        import psycopg  # noqa: F401
        return psycopg
    except ImportError:
        sys.exit(
            "psycopg is not installed. Install it first:\n"
            '    pip install "psycopg[binary]"'
        )


def _resolve_connection() -> dict:
    """Return psycopg.connect kwargs, from a Lakebase instance (via SDK) or PG* vars."""
    instance_name = os.getenv("LAKEBASE_INSTANCE_NAME", "").strip()

    # Mode B — explicit Postgres credentials.
    if not instance_name:
        host = os.getenv("PGHOST", "").strip()
        if not host:
            sys.exit(
                "No connection configured.\n"
                "  Set LAKEBASE_INSTANCE_NAME (Lakebase, recommended), OR\n"
                "  set PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (direct Postgres)."
            )
        print(f"[mode] Direct Postgres  host={host}")
        return {
            "host": host,
            "port": int(os.getenv("PGPORT", "5432")),
            "dbname": os.getenv("PGDATABASE", "postgres"),
            "user": os.getenv("PGUSER", ""),
            "password": os.getenv("PGPASSWORD", ""),
            "sslmode": os.getenv("PGSSLMODE", "require"),
        }

    # Mode A — Lakebase via Databricks SDK (uses DATABRICKS_HOST + DATABRICKS_TOKEN).
    from databricks.sdk import WorkspaceClient

    print(f"[mode] Lakebase via Databricks SDK  instance={instance_name}")
    w = WorkspaceClient()  # picks up DATABRICKS_HOST / DATABRICKS_TOKEN from env

    instance = w.database.get_database_instance(name=instance_name)
    host = instance.read_write_dns
    if not host:
        sys.exit(f"Lakebase instance '{instance_name}' has no read_write_dns — is it running?")

    cred = w.database.generate_database_credential(
        request_id=str(uuid.uuid4()),
        instance_names=[instance_name],
    )
    token = cred.token
    user = os.getenv("PGUSER", "").strip() or w.current_user.me().user_name

    print(f"[mode] Lakebase host={host}  user={user}")
    return {
        "host": host,
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", "databricks_postgres"),
        "user": user,
        "password": token,
        "sslmode": os.getenv("PGSSLMODE", "require"),
    }


def main() -> None:
    _load_dotenv_if_present()
    psycopg = _require_psycopg()
    conn_kwargs = _resolve_connection()

    print("[1/5] Connecting ...")
    with psycopg.connect(**conn_kwargs, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            print(f"[2/5] Connected. Server: {cur.fetchone()[0]}")

            print(f"[3/5] Creating table {_TEST_TABLE} (if not exists) ...")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TEST_TABLE} (
                    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    note        TEXT NOT NULL,
                    written_at  TIMESTAMPTZ NOT NULL
                );
                """
            )

            note = f"postgres access test @ {uuid.uuid4().hex[:8]}"
            cur.execute(
                f"INSERT INTO {_TEST_TABLE} (note, written_at) VALUES (%s, %s) RETURNING id;",
                (note, datetime.now(timezone.utc)),
            )
            new_id = cur.fetchone()[0]
            print(f"[4/5] Inserted row id={new_id}  note='{note}'")

            conn.commit()

            cur.execute(
                f"SELECT id, note, written_at FROM {_TEST_TABLE} ORDER BY id DESC LIMIT 5;"
            )
            rows = cur.fetchall()
            print(f"[5/5] Read back last {len(rows)} row(s):")
            for r in rows:
                print(f"      id={r[0]} | {r[1]} | {r[2].isoformat()}")

    print("\n✅ SUCCESS — Postgres is reachable and writable with your credentials.")
    print(f"   Clean up when done:  DROP TABLE {_TEST_TABLE};")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ FAILED — {type(exc).__name__}: {exc}")
        sys.exit(1)
