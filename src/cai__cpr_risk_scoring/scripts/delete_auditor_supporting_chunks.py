"""Delete ONE auditor's chunks from the supporting-docs vector index (surgical cleanup).

Use this after a mis-scoped ingestion — e.g. when supporting_docs_path pointed at the PARENT
`auditors_supporting_docs` folder and every file got embedded under a single auditor_folder label.
All of those chunks carry `auditor_folder=<AUDITOR>`, so deleting by that label removes exactly the
polluted batch WITHOUT dropping/recreating the whole index (other auditors' correctly-scoped chunks,
if any, are untouched).

Usage:
    python scripts/delete_auditor_supporting_chunks.py --auditor Naeem_6
    python scripts/delete_auditor_supporting_chunks.py --auditor Naeem_6 --index supporting
    python scripts/delete_auditor_supporting_chunks.py --auditor Naeem_6 --yes   # skip confirmation

After it finishes, re-run Risk Analysis for that auditor with the CORRECT path
(`/Volumes/.../auditors_supporting_docs/<AUDITOR>`, or leave supporting_docs_path empty so the engine
derives it) and force_ingest=true.
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete one auditor's chunks from a vector index")
    parser.add_argument("--auditor", required=True, help="auditor_folder whose chunks to delete (e.g. Naeem_6)")
    parser.add_argument("--index", default="supporting",
                        help="index key (default: supporting). Others: si_history, legal, "
                             "additional_input, past_audit")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    settings = get_settings()
    embedding_provider = AzureOpenAIEmbeddingProvider(settings)
    index_manager = VectorIndexManager(settings, embedding_provider)

    index_name = index_manager._index_names.get(args.index, args.index)
    logger.info("[Cleanup] Target | index_key=%s | index=%s | auditor_folder=%s",
                args.index, index_name, args.auditor)

    if not args.yes:
        reply = input(
            f"Delete ALL chunks with auditor_folder='{args.auditor}' from '{index_name}'? "
            f"This cannot be undone. Type 'yes' to proceed: "
        ).strip().lower()
        if reply != "yes":
            logger.info("[Cleanup] Aborted — no changes made.")
            return

    index_manager.ensure_index(args.index)
    deleted = index_manager.delete_by_auditor_folder(args.index, args.auditor)
    logger.info("[Cleanup] Done | deleted %d chunk(s) for auditor_folder='%s' from %s",
                deleted, args.auditor, index_name)
    if deleted == 0:
        logger.info("[Cleanup] Nothing matched — the index may already be clean for this auditor.")


if __name__ == "__main__":
    main()
