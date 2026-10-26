"""
Force re-ingest of the fcpa_scope_policy_index (the CPR Audit Guidance index).

Use this after changing CPRScopeExtractor (e.g. adding columns G, H, F, I, J, K).
The normal pipeline SKIPS re-ingestion when the index is already populated
(_ensure_scope_policy_index → is_populated_any), so new columns never reach the live
index. This script deletes the index, recreates it empty, and re-feeds fresh chunks
UNCONDITIONALLY.

Usage:
    python scripts/reingest_scope_policy_index.py
    python scripts/reingest_scope_policy_index.py --verify-only   # inspect, don't re-ingest

Steps:
    1. Delete + recreate fcpa_scope_policy_index (blank slate)
    2. Download CPR AI - System Guide.xlsx from the Databricks volume
    3. Extract chunks with the CURRENT CPRScopeExtractor (incl. sampling_guidance)
    4. Upsert in batches and wait until the index is queryable
    5. Print a verification sample so you can confirm the new columns are present
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.ingestion.extractors.cpr_scope_extractor import CPRScopeExtractor
from src.ingestion.volume_downloader import download_volume_file
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger(__name__)

_INDEX_KEY = "scope_policy"
_BATCH_SIZE = 50


def _download_cpr(settings) -> Path | None:
    cache_dir = _PROJECT_ROOT / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_cpr = cache_dir / "CPR AI - System Guide.xlsx"

    logger.info("[Reingest] Downloading CPR file | path=%s", settings.scope_area_policy_file_path)
    ok = download_volume_file(
        settings.databricks_host,
        settings.databricks_token,
        settings.scope_area_policy_file_path,
        local_cpr,
    )
    if not ok:
        logger.error("[Reingest] CPR file download FAILED — aborting")
        return None
    return local_cpr


def _extract_chunks(local_cpr: Path):
    chunks = CPRScopeExtractor().extract(local_cpr)
    if not chunks:
        logger.error("[Reingest] CPR extractor returned NO chunks — aborting")
        return []

    # Quick breakdown by chunk_type so you can confirm sampling_guidance exists.
    by_type: dict[str, int] = {}
    for c in chunks:
        t = c.metadata.get("chunk_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    logger.info("[Reingest] Extracted %d chunks | by type=%s", len(chunks), by_type)

    # Show one full example so you can eyeball the new columns (G/H/F/I/J/K).
    sample = next((c for c in chunks if c.metadata.get("chunk_type") == "risk_profile"), chunks[0])
    logger.info(
        "[Reingest] Sample chunk | id=%s | scope=%s\n--- CONTENT START ---\n%s\n--- CONTENT END ---",
        sample.chunk_id, sample.scope_id, sample.content[:1500],
    )
    return chunks


def _reingest(index_manager, settings) -> None:
    # NO index deletion (avoids the "pending deletion" lock and needs no delete permission).
    # Chunk IDs are deterministic ({scope}_risk_profile / _audit_guidance / _sampling_guidance),
    # so upserting overwrites every existing row in place and adds the new sampling_guidance
    # rows — effectively clearing and refreshing the index content without dropping the index.
    logger.info("[Reingest] Ensuring %s exists (no delete) ...", _INDEX_KEY)
    index_manager.ensure_index(_INDEX_KEY)
    logger.info("[Reingest] Waiting for %s to be query-ready before upsert ...", _INDEX_KEY)
    index_manager.wait_until_ready(_INDEX_KEY)

    local_cpr = _download_cpr(settings)
    if local_cpr is None:
        return

    chunks = _extract_chunks(local_cpr)
    if not chunks:
        return

    total = len(chunks)
    batches = [chunks[i : i + _BATCH_SIZE] for i in range(0, total, _BATCH_SIZE)]
    logger.info("[Reingest] Upserting %d chunks in %d batch(es) ...", total, len(batches))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(index_manager.upsert_chunks, _INDEX_KEY, b): b for b in batches}
        for fut in as_completed(futures):
            fut.result(timeout=120)

    logger.info("[Reingest] Waiting for index to become queryable ...")
    index_manager.wait_until_ready(_INDEX_KEY)
    logger.info("[Reingest] DONE — %s repopulated with %d chunks.", _INDEX_KEY, total)


def _verify(index_manager) -> None:
    """Query the live index for a known scope and print what comes back."""
    logger.info("[Verify] Probing live index for LOGISTICS_INTERMEDIARIES__CUSTOMS ...")
    chunks = index_manager.similarity_search(
        index_key=_INDEX_KEY,
        query="expected sample size sampling rationale data calculations name of the data",
        scope_id="LOGISTICS_INTERMEDIARIES__CUSTOMS",
        top_k=5,
    )
    if not chunks:
        logger.warning("[Verify] No chunks returned — index may be empty or scope_id not present.")
        return
    for c in chunks:
        logger.info(
            "[Verify] chunk_type=%s | scope=%s\n%s\n%s",
            c.metadata.get("chunk_type"), c.scope_id, c.content[:800], "-" * 60,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Force re-ingest of fcpa_scope_policy_index")
    parser.add_argument("--verify-only", action="store_true",
                        help="Skip re-ingestion; just query the live index and print results")
    args = parser.parse_args()

    settings = get_settings()
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)

    if args.verify_only:
        index_manager.ensure_index(_INDEX_KEY)
        _verify(index_manager)
        return

    _reingest(index_manager, settings)
    _verify(index_manager)


if __name__ == "__main__":
    main()
