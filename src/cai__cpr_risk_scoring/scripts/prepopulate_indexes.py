"""Provision (create + wait-ready) EVERY vector index on the configured endpoint — NO ingestion.

Purpose: pre-create all the direct-access indexes so the main run's ingestion never fails on a
missing / not-yet-ready index (index creation + readiness can race the first upsert). This only
CREATES empty indexes with the correct schema and waits until each is query/write ready; the actual
run then fills them.

Respects INDEX_NAME_SUFFIX and DATABRICKS_VECTOR_SEARCH_ENDPOINT, so it provisions the SAME indexes
the run will use (e.g. an isolated run on a new endpoint).

Run (venv active, .env configured; same endpoint/suffix env as the run):
    python scripts/prepopulate_indexes.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))


def _fix_tls() -> None:
    """Validate TLS against the OS trust store so the corporate root CA is trusted (SSL fix).

    Same approach the app uses (see services/translation.py::_ensure_os_truststore). Must run BEFORE
    any Databricks/HTTPS client is built. Best-effort: if `truststore` is unavailable, continue.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass


_fix_tls()

from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402
from src.vector_store.index_manager import VectorIndexManager  # noqa: E402

logger = get_logger("prepopulate_indexes")

_QUESTIONNAIRE_KEY = "questionnaire"  # different schema → use ensure_questionnaire_index()


def _step(label: str, fn) -> bool:
    """Run one provision step; log start/outcome; never abort the whole run on one failure."""
    logger.info("[Provision] %s …", label)
    try:
        fn()
        logger.info("[Provision] %s — OK", label)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("[Provision] %s — FAILED | %s", label, exc, exc_info=True)
        return False


def main() -> int:
    argparse.ArgumentParser(description="Create (provision) all vector indexes on the endpoint; no ingestion.").parse_args()

    settings = get_settings()
    index_manager = VectorIndexManager(settings, AzureOpenAIEmbeddingProvider(settings))

    logger.info("[Provision] START | endpoint=%s | suffix=%r",
                settings.databricks_vector_search_endpoint, getattr(settings, "index_name_suffix", ""))

    ok = 0
    total = 0

    # All generic direct-access indexes (same document schema). questionnaire handled separately below.
    for key in index_manager._index_names:
        if key == _QUESTIONNAIRE_KEY:
            continue
        total += 1

        def _create(k=key):
            index_manager.ensure_index(k)        # create if absent
            index_manager.wait_until_ready(k)    # block until query/write ready

        if _step(f"index '{key}'", _create):
            ok += 1

    # Questionnaire index — questionnaire-shaped schema, its own ensure method (also waits ready).
    total += 1
    if _step(f"index '{_QUESTIONNAIRE_KEY}'", index_manager.ensure_questionnaire_index):
        ok += 1

    logger.info("[Provision] DONE | %d/%d indexes ready | endpoint=%s",
                ok, total, settings.databricks_vector_search_endpoint)
    # NOTE: the Scoping Deck's past_review index is created by the deck run itself (separate manager);
    # it is small and not part of Risk Analysis ingestion, so it is not provisioned here.
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
