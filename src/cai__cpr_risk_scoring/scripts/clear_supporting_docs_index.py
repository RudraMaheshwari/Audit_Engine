"""
Deletes and recreates the auditor_supporting_docs_index and fcpa_scope_policy_index
on Databricks Vector Search.
Run this before re-ingesting to start from a clean slate.

Usage:
    python scripts/clear_supporting_docs_index.py [--supporting] [--scope-policy] [--all]

    --supporting    Clear only auditor_supporting_docs_index
    --scope-policy  Clear only fcpa_scope_policy_index
    --all           Clear both (default when no flag given)
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import get_settings
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

logger = get_logger(__name__)


def _clear_index(index_manager: VectorIndexManager, key: str, display_name: str) -> None:
    logger.info("[Clear] Deleting %s ...", display_name)
    index_manager.delete_index(key)
    logger.info("[Clear] Recreating %s ...", display_name)
    index_manager.ensure_index(key)
    logger.info("[Clear] %s is now empty and ready for ingestion.", display_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear vector search indexes")
    parser.add_argument("--supporting",   action="store_true", help="Clear auditor_supporting_docs_index")
    parser.add_argument("--scope-policy", action="store_true", help="Clear fcpa_scope_policy_index")
    parser.add_argument("--all",          action="store_true", help="Clear both indexes (default)")
    args = parser.parse_args()

    run_supporting  = args.supporting or args.all or not (args.supporting or args.scope_policy)
    run_scope_policy = args.scope_policy or args.all or not (args.supporting or args.scope_policy)

    settings = get_settings()
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)

    if run_supporting:
        _clear_index(index_manager, "supporting", "auditor_supporting_docs_index")

    if run_scope_policy:
        _clear_index(index_manager, "scope_policy", "fcpa_scope_policy_index")

    logger.info("[Clear] Done.")


if __name__ == "__main__":
    main()
