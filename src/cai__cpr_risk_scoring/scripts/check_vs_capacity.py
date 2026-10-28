"""Quick capacity probe for the vector-search endpoint: lists every index + its indexed row count
and status, so you can see what is actually consuming the endpoint quota.

    python scripts/check_vs_capacity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))

from databricks.sdk import WorkspaceClient  # noqa: E402

from src.config.databricks_auth import build_config  # noqa: E402
from src.config.settings import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    w = WorkspaceClient(config=build_config(s))
    endpoint = s.databricks_vector_search_endpoint

    ep = w.vector_search_endpoints.get_endpoint(endpoint_name=endpoint)
    print(f"\nENDPOINT: {endpoint}")
    print(f"  type   : {getattr(ep, 'endpoint_type', '?')}")
    print(f"  state  : {getattr(getattr(ep, 'endpoint_status', None), 'state', '?')}")
    ns = getattr(ep, "num_indexes", None)
    if ns is not None:
        print(f"  indexes: {ns}")

    print("\nINDEXES on this endpoint:")
    total_rows = 0
    try:
        idxs = list(w.vector_search_indexes.list_indexes(endpoint_name=endpoint))
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not list indexes: {exc})")
        idxs = []

    for it in idxs:
        name = getattr(it, "name", "?")
        try:
            d = w.vector_search_indexes.get_index(index_name=name)
            st = getattr(d, "status", None)
            rows = getattr(st, "indexed_row_count", None) or getattr(st, "row_count", None) or 0
            ready = getattr(st, "ready", "?")
            total_rows += int(rows or 0)
            print(f"  - {name:60s} rows={rows:>10} ready={ready}")
        except Exception as exc:  # noqa: BLE001
            print(f"  - {name:60s} (describe failed: {exc})")

    print(f"\nTOTAL indexed rows across endpoint: {total_rows}")
    print("NOTE: 'capacity limit' on a Standard endpoint is usually the NUMBER OF INDEXES and total\n"
          "vectors — deleting ROWS may not free it; deleting whole unused INDEXES does.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
