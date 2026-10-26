"""
check_indexes.py — Diagnostic: are the 3 vector search indexes populated?
Lists all unique document names stored in each index.

Usage:
    python scripts/check_indexes.py

Uses a zero-vector query so no embedding API key is needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from databricks.vector_search.client import VectorSearchClient
from src.config.settings import get_settings


def scan_index(client, endpoint: str, index_name: str, dimensions: int, scope_id: str):
    """
    Query the index to retrieve whatever is stored, without needing a live embedding API.
    Uses a small epsilon vector (NOT zero — zero vector yields undefined cosine similarity
    and Databricks returns 0 results for it).  Returns (scoped_rows, unscoped_rows).
    """
    # A tiny non-zero vector gives valid cosine/dot-product scores and returns real results.
    epsilon_vector = [1e-6] * dimensions

    index = client.get_index(endpoint_name=endpoint, index_name=index_name)

    # First pass: query scoped to current scope_id
    try:
        scoped = index.similarity_search(
            columns=["chunk_id", "document_name", "source", "scope_id", "token_count"],
            query_vector=epsilon_vector,
            filters={"scope_id": scope_id},
            num_results=50,
        )
        scoped_rows = scoped.get("result", {}).get("data_array", [])
    except Exception as exc:
        scoped_rows = []
        print(f"   [WARN] Scoped query failed: {exc}")

    # Second pass: no scope filter — see everything in the index
    try:
        unscoped = index.similarity_search(
            columns=["chunk_id", "document_name", "source", "scope_id", "token_count"],
            query_vector=epsilon_vector,
            num_results=100,
        )
        unscoped_rows = unscoped.get("result", {}).get("data_array", [])
    except Exception as exc:
        unscoped_rows = []
        print(f"   [WARN] Unscoped query failed: {exc}")

    return scoped_rows, unscoped_rows


def count_rows_via_sql(settings, index_name: str) -> int | None:
    """
    Fallback: count rows in the underlying Delta table via the SQL warehouse.
    This is authoritative — bypasses the Vector Search API entirely.
    index_name is the fully qualified table name (catalog.schema.table).
    Returns the row count, or None if the query fails.
    """
    try:
        from databricks import sql as dbsql
        conn = dbsql.connect(
            server_hostname=settings.databricks_host.replace("https://", ""),
            http_path=f"/sql/1.0/warehouses/{settings.databricks_warehouse_id}",
            access_token=settings.databricks_token,
        )
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {index_name}")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        print(f"   [SQL-ERROR] {exc}")
        return None


def format_rows(rows: list) -> dict[str, dict]:
    """
    Deduplicate by document_name and count chunks per document.
    Returns {doc_name: {"source": ..., "scope_id": ..., "chunks": N}}
    """
    docs: dict[str, dict] = {}
    for row in rows:
        doc_name  = row[1] if len(row) > 1 else "unknown"
        source    = row[2] if len(row) > 2 else "unknown"
        scope     = row[3] if len(row) > 3 else "unknown"
        tok       = row[4] if len(row) > 4 else 0
        if doc_name not in docs:
            docs[doc_name] = {"source": source, "scope_id": scope, "chunks": 0, "tokens": 0}
        docs[doc_name]["chunks"] += 1
        docs[doc_name]["tokens"] += (tok or 0)
    return docs


def main() -> None:
    settings = get_settings()
    prefix   = f"{settings.databricks_catalog}.{settings.databricks_schema_name}"
    endpoint = settings.databricks_vector_search_endpoint
    dims     = settings.embedding_dimensions
    scope_id = settings.current_scope_id

    indexes = {
        "HCBI":       f"{prefix}.{settings.hcbi_index_name}",
        "POLICY":     f"{prefix}.{settings.policy_index_name}",
        "SUPPORTING": f"{prefix}.{settings.supporting_docs_index_name}",
    }

    client = VectorSearchClient(
        workspace_url=settings.databricks_host,
        personal_access_token=settings.databricks_token,
        disable_notice=True,
    )

    print("\n" + "=" * 72)
    print(f"  Endpoint  : {endpoint}")
    print(f"  Scope     : {scope_id}")
    print(f"  Dimensions: {dims}")
    print("=" * 72)

    for label, full_name in indexes.items():
        print(f"\n{'─'*72}")
        print(f"  {label} INDEX — {full_name}")
        print(f"{'─'*72}")

        # Check existence
        try:
            client.get_index(endpoint_name=endpoint, index_name=full_name)
        except Exception as exc:
            print(f"  [ERROR] Index does not exist or cannot be reached: {exc}")
            continue

        print(f"  Index exists: YES")

        # --- Authoritative: index description from the endpoint ---------------
        try:
            idx_obj = client.get_index(endpoint_name=endpoint, index_name=full_name)
            desc = idx_obj.describe()
            # The SDK may return stats nested differently depending on version
            status    = desc.get("status", {})
            num_vecs  = status.get("num_indexed_rows") or status.get("indexed_row_count") or desc.get("num_indexed_rows", "N/A")
            idx_status = status.get("message") or status.get("state") or desc.get("status", "N/A")
            print(f"  Index status : {idx_status}")
            print(f"  Indexed rows : {num_vecs}  ← authoritative count from endpoint")
            print(f"  Full describe: {desc}")
        except Exception as exc:
            print(f"  [WARN] describe() failed: {exc}")

        scoped_rows, unscoped_rows = scan_index(client, endpoint, full_name, dims, scope_id)

        scoped_docs   = format_rows(scoped_rows)
        unscoped_docs = format_rows(unscoped_rows)

        # --- SQL row count (authoritative) ---
        sql_count = count_rows_via_sql(settings, full_name)
        if sql_count is not None:
            print(f"  SQL COUNT (authoritative): {sql_count:,} total rows in Delta table")
        else:
            print(f"  SQL COUNT: unavailable (warehouse query failed — see WARN above)")

        # --- Scoped results ---
        print(f"\n  Vector search results for scope_id='{scope_id}': {len(scoped_rows)} chunk(s) (sample, max 50)")
        if scoped_docs:
            print(f"  {'Document Name':<50} {'Chunks':>6}  {'Tokens':>8}")
            print(f"  {'-'*50}  {'-'*6}  {'-'*8}")
            for doc, info in sorted(scoped_docs.items()):
                name_trunc = doc[:50] if len(doc) > 50 else doc
                print(f"  {name_trunc:<50} {info['chunks']:>6}  {info['tokens']:>8}")
        else:
            print(f"  [!] No data found for scope '{scope_id}' via vector search")

        # --- All data (unscoped) ---
        if unscoped_docs and not scoped_docs:
            other_scopes = {info["scope_id"] for info in unscoped_docs.values()}
            print(f"\n  BUT index has data under other scope(s): {other_scopes}")
            print(f"  Total chunks across all scopes (sample): {len(unscoped_rows)}")
            print(f"  {'Document Name':<50} {'Scope':<25} {'Chunks':>6}")
            print(f"  {'-'*50}  {'-'*25}  {'-'*6}")
            for doc, info in sorted(unscoped_docs.items()):
                name_trunc = doc[:50] if len(doc) > 50 else doc
                print(f"  {name_trunc:<50}  {info['scope_id']:<25}  {info['chunks']:>6}")
        elif not unscoped_docs:
            if sql_count and sql_count > 0:
                print(f"\n  [!] Vector search returned 0 but SQL shows {sql_count:,} rows.")
                print(f"      Likely cause: epsilon vector query hit an API issue. Data IS present.")
            else:
                print(f"\n  [!] Index is completely EMPTY — ingestion has not run for this index.")
        elif scoped_docs:
            total_all = len(unscoped_rows)
            total_scoped = len(scoped_rows)
            if total_all > total_scoped:
                print(f"\n  (Index also has {total_all - total_scoped} chunk(s) under other scopes)")

    print("\n" + "=" * 72 + "\n")


if __name__ == "__main__":
    main()
