"""TEST ONLY — inspect what Section / Page No. metadata is actually stored per chunk.

For a sample of chunks from each index, prints the document name and ALL metadata keys
(especially section / section_no / section_title / page_number) so we can see whether
Section and Page No. can be surfaced in Source Details BEFORE changing any writer code.

Run:
    python scripts/test_source_metadata.py

Env overrides:
    set TEST_INDEXES=hcbi,policy,supporting     (index keys to inspect; default hcbi,policy)
    set TEST_QUERY=due diligence approval fair market value
    set TEST_TOPK=5
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make `src` importable no matter where this is run from (repo root or scripts/).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env from the repo root (pydantic otherwise only looks in the current directory).
from dotenv import load_dotenv  # noqa: E402
load_dotenv()                     # CWD, if run from root
load_dotenv(_ROOT / ".env")       # explicit repo-root .env, if run from scripts/

from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.vector_store.index_manager import VectorIndexManager


def main() -> int:
    settings = get_settings()

    indexes = [x.strip() for x in os.environ.get("TEST_INDEXES", "hcbi,policy").split(",") if x.strip()]
    query = os.environ.get("TEST_QUERY", "compliance policy requirement approval due diligence")
    top_k = int(os.environ.get("TEST_TOPK", "5"))

    emb = AzureOpenAIEmbeddingProvider(settings)
    im = VectorIndexManager(settings, emb)

    for key in indexes:
        index_name = im._index_names.get(key, "(unknown key)")
        print("\n" + "=" * 100)
        print(f"INDEX key='{key}'  ->  {index_name}")
        print("=" * 100)

        try:
            chunks = im.similarity_search(index_key=key, query=query, scope_id=None, top_k=top_k)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR querying '{key}': {exc}")
            continue

        if not chunks:
            print("  (no chunks returned — index empty or not populated?)")
            continue

        for i, c in enumerate(chunks, 1):
            md = c.metadata or {}
            print(f"\n[{i}] document_name = {c.document_name}")
            print(f"    scope_id       = {c.scope_id}")
            print(f"    section (norm) = {md.get('section')!r}")
            print(f"    page_number    = {md.get('page_number')!r}")
            print(f"    section_no     = {md.get('section_no')!r}")
            print(f"    section_title  = {md.get('section_title')!r}")
            print(f"    ALL metadata keys : {sorted(md.keys())}")
            try:
                print(f"    ALL metadata      : {json.dumps(md, default=str)[:1500]}")
            except Exception:
                print(f"    ALL metadata      : {md}")

    print("\n" + "=" * 100)
    print("DONE. If section/page_number are empty/None above, the values were NOT captured at")
    print("ingestion (so 'Not Available' is truthful) — surfacing them needs the extractor/chunker")
    print("to record page + section per chunk, then a re-ingest.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
