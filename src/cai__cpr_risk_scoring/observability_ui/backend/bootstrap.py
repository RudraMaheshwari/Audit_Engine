"""Make the main app importable and load its environment once.

The backend reuses the main app's Settings, Lakebase connection, Databricks SQL engine, embeddings and
LLM — so observability has a single source of truth. This module puts the project root on sys.path and
loads config (.env + Key Vault) exactly like the API does.
"""

from __future__ import annotations

import sys
from pathlib import Path

# observability_ui/backend/bootstrap.py -> backend -> observability_ui -> <project root>
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_loaded = False


def ensure_config() -> None:
    global _loaded
    if _loaded:
        return
    try:
        from src.config.config_loader import load_config

        load_config()
    except Exception:  # noqa: BLE001 — fall back to plain .env
        from dotenv import load_dotenv

        load_dotenv()
    _loaded = True


def settings():
    ensure_config()
    from src.config.settings import get_settings

    return get_settings()
