"""
list_policy_docs.py — Lists all document names stored in due_diligence_index.

Usage:
    python scripts/list_policy_docs.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from databricks.vector_search.client import VectorSearchClient
from src.config.settings import get_settings

settings  = get_settings()
prefix    = f"{settings.databricks_catalog}.{settings.databricks_schema_name}"
index_name = f"{prefix}.{settings.policy_index_name}"
endpoint  = settings.databricks_vector_search_endpoint
epsilon   = [1e-6] * settings.embedding_dimensions

client = VectorSearchClient(
    workspace_url=settings.databricks_host,
    personal_access_token=settings.databricks_token,
    disable_notice=True,
)

index = client.get_index(endpoint_name=endpoint, index_name=index_name)

result = index.similarity_search(
    columns=["chunk_id", "document_name", "scope_id", "token_count"],
    query_vector=epsilon,
    num_results=500,
)

rows = result.get("result", {}).get("data_array", [])

# Deduplicate by document_name and count chunks
docs: dict[str, dict] = {}
for row in rows:
    name   = row[1] if len(row) > 1 else "unknown"
    scope  = row[2] if len(row) > 2 else "unknown"
    tokens = row[3] if len(row) > 3 else 0
    if name not in docs:
        docs[name] = {"scope": scope, "chunks": 0, "tokens": 0}
    docs[name]["chunks"] += 1
    docs[name]["tokens"] += (tokens or 0)

print(f"\n{'='*70}")
print(f"  Index : {index_name}")
print(f"  Total documents : {len(docs)}")
print(f"  Total chunks (sample, max 500) : {len(rows)}")
print(f"{'='*70}")
print(f"  {'Document Name':<50} {'Chunks':>6}  {'Tokens':>8}")
print(f"  {'-'*50}  {'-'*6}  {'-'*8}")
for name, info in sorted(docs.items()):
    print(f"  {name[:50]:<50}  {info['chunks']:>6}  {info['tokens']:>8}")
print(f"{'='*70}\n")
