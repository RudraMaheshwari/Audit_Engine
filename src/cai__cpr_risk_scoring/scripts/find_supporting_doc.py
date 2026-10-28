"""Find a file in the supporting-docs Vector Search index and show WHERE it was picked up from.

Uses a FILTERED similarity_search (filter: auditor_folder) with COLUMN PROJECTION (document_name,
source only — no content/embedding) so one small request returns the auditor's files instantly. No
full-index scan. Matches the file name locally and prints each file's source path + chunk count.

Examples:
    python scripts/find_supporting_doc.py --auditor Naeem_7 --file "HCC related activities"
    python scripts/find_supporting_doc.py --auditor Naeem_7                      # list ALL files
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))

from databricks.vector_search.client import VectorSearchClient  # noqa: E402

from src.config.settings import get_settings  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("find_supporting_doc")

_NUM_RESULTS = 10000  # index hard cap; projected columns keep the payload small


def main() -> int:
    p = argparse.ArgumentParser(description="Find a file in the supporting-docs index and show its source.")
    p.add_argument("--auditor", "--auditor-folder", dest="auditor", required=True,
                   help="auditor_folder to search within (e.g. Naeem_7)")
    p.add_argument("--file", dest="file", default="",
                   help="substring to match in document_name / source (case-insensitive). "
                        "Omit to list ALL files for the auditor.")
    args = p.parse_args()

    auditor = args.auditor.strip()
    needle = args.file.strip().lower()

    settings = get_settings()
    index_name = (f"{settings.databricks_catalog}.{settings.databricks_schema_name}."
                  f"{settings.supporting_docs_index_name}")

    client = VectorSearchClient(
        workspace_url=settings.databricks_host,
        service_principal_client_id=settings.databricks_client_id,
        service_principal_client_secret=settings.databricks_client_secret,
        disable_notice=True,
    )
    index = client.get_index(
        endpoint_name=settings.databricks_vector_search_endpoint, index_name=index_name
    )

    logger.info("[FindDoc] Filtered query | index=%s | auditor_folder=%s | match=%r",
                index_name, auditor, args.file or "(all)")

    # Pure filter query: epsilon vector (we don't care about ranking) + auditor_folder filter, and we
    # PROJECT only the two columns we need so the response stays tiny even for thousands of rows.
    res = index.similarity_search(
        columns=["document_name", "source"],
        query_vector=[1e-6] * settings.embedding_dimensions,
        filters={"auditor_folder": auditor},
        num_results=_NUM_RESULTS,
    )
    rows = res.get("result", {}).get("data_array", []) or []

    hits: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        doc = (row[0] if len(row) > 0 else "") or ""
        src = (row[1] if len(row) > 1 else "") or ""
        if needle and needle not in f"{doc}\n{src}".lower():
            continue
        hits[(doc, src)] += 1

    print(f"\nindex          : {index_name}")
    print(f"auditor_folder : {auditor}")
    print(f"match text     : {args.file or '(all files)'}")
    print(f"rows returned  : {len(rows)}"
          + ("  (CAPPED at 10000 — auditor may have more)" if len(rows) >= _NUM_RESULTS else ""))

    if not hits:
        print("\nNO MATCH — not found in the supporting-docs index for that auditor.\n")
        return 0

    print(f"\nMATCHED {len(hits)} file(s):\n")
    for (doc, src), n in sorted(hits.items(), key=lambda kv: -kv[1]):
        print(f"  document_name : {doc}")
        print(f"  source        : {src or '(empty)'}        <-- where it was picked up from")
        print(f"  chunks        : {n}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
