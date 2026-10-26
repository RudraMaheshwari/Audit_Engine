"""Project entrypoint — serves the AI Auditor API (Risk Analysis, Scoping AI, Scoping Deck, Admin).

Run:
    python main.py                      # serve on API_HOST:API_PORT
    uvicorn main:app --reload           # dev, with autoreload
"""

from dotenv import load_dotenv

# Always load .env FIRST so the app runs even when Key Vault is not yet accessible. load_config() then
# overlays Azure Key Vault secrets when reachable (best-effort — it falls back to .env if it can't
# connect, unless KEY_VAULT_STRICT=true). Both run before any settings are read.
load_dotenv()

try:
    from src.config.config_loader import load_config

    load_config()
except Exception as _cfg_exc:  # noqa: BLE001 — never let the config overlay block startup
    import logging

    logging.getLogger(__name__).warning(
        "Key Vault config overlay skipped — using .env only | %s", _cfg_exc
    )

from app.application import create_app  # noqa: E402  (after config is loaded so settings see the env)

app = create_app()


def main() -> None:
    import uvicorn

    from app.core.config import get_api_settings

    settings = get_api_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
