"""Centralized environment/configuration loader.

Responsibilities:
    1. Load the local `.env` file (override=False, so AKS ConfigMap / shell env wins).
    2. Authenticate to Azure using DefaultAzureCredential.
    3. Fetch configured SECRETS from Azure Key Vault.
    4. Inject them into os.environ under this app's env-var names, so the existing pydantic settings
       (src.config.settings.Settings and app.core.config.ApiSettings) pick them up unchanged.

Usage:
    Call `load_config()` ONCE at application startup — BEFORE any Settings()/get_settings() is read.
    Do not call `load_dotenv()` elsewhere in the codebase.

Local vs AKS:
    * Local  — DefaultAzureCredential uses AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET from
               .env to reach the vault (these are the BOOTSTRAP credential and cannot be loaded FROM the
               vault locally).
    * AKS    — the pod's managed / workload identity is used automatically; no SP secret needed in env.

Key Vault can be turned OFF for pure .env local dev with KEY_VAULT_ENABLED=false.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Default vault for this project; override with AZURE_KEY_VAULT_URL (full URL) or AZURE_KEY_VAULT_NAME.
_DEFAULT_KEY_VAULT_NAME: Final[str] = "gaa-cpr-risk-analysis-kv-dev"

# Mapping: Azure Key Vault secret name -> this app's environment variable name.
# NOTE: Key Vault object names may contain only letters, digits, and hyphens — NO underscores — so each
# secret is the env-var name with '_' replaced by '-' ("same names" convention). Adjust a left-hand key
# here if a secret is actually named differently in the vault.
#
# This vault holds the project's FULL shared configuration (endpoints, deployment names, keys,
# catalog/schema, flags) — the fixed values that are identical across runs — not just raw secrets. A
# value present in the vault OVERRIDES any .env value for that key (vault is authoritative). Per-run /
# per-auditor inputs (auditor_id, engagement numbers, volume paths, index names, etc.) are NOT here —
# they stay in .env / the ConfigMap / the API request.
_KEY_VAULT_SECRET_MAPPING: Final[dict[str, str]] = {
    # ── Azure OpenAI (chat) ──────────────────────────────────────────────────
    "AZURE-OPENAI-ENDPOINT":                "AZURE_OPENAI_ENDPOINT",
    "AZURE-OPENAI-API-KEY":                 "AZURE_OPENAI_API_KEY",
    "AZURE-OPENAI-DEPLOYMENT":              "AZURE_OPENAI_DEPLOYMENT",
    "AZURE-OPENAI-API-VERSION":             "AZURE_OPENAI_API_VERSION",
    # ── Azure OpenAI embeddings ──────────────────────────────────────────────
    "AZURE-EMBEDDING-ENDPOINT":             "AZURE_EMBEDDING_ENDPOINT",
    "AZURE-EMBEDDING-API-KEY":              "AZURE_EMBEDDING_API_KEY",
    "AZURE-EMBEDDING-DEPLOYMENT":           "AZURE_EMBEDDING_DEPLOYMENT",
    "EMBEDDING-DIMENSIONS":                 "EMBEDDING_DIMENSIONS",
    # ── Azure Translator (text + document) ───────────────────────────────────
    "AZURE-TRANSLATOR-ENDPOINT":            "AZURE_TRANSLATOR_ENDPOINT",
    "AZURE-TRANSLATOR-KEY":                 "AZURE_TRANSLATOR_KEY",
    "AZURE-TRANSLATOR-REGION":              "AZURE_TRANSLATOR_REGION",
    "TRANSLATION-ENGINE":                   "TRANSLATION_ENGINE",
    "AZURE-TRANSLATOR-DOCUMENT-ENDPOINT":   "AZURE_TRANSLATOR_DOCUMENT_ENDPOINT",
    # ── Azure Storage (document-translation staging) ─────────────────────────
    "AZURE-STORAGE-ACCOUNT":                "AZURE_STORAGE_ACCOUNT",
    "AZURE-STORAGE-CONTAINER":              "AZURE_STORAGE_CONTAINER",
    # ── Azure AD service principal ───────────────────────────────────────────
    # These are also the KV BOOTSTRAP credential locally (DefaultAzureCredential needs them from .env to
    # reach the vault in the first place); on AKS the pod's managed identity is used instead. Overlaying
    # them from the vault is safe — the .env value and the vault value are the same SP.
    "AZURE-TENANT-ID":                      "AZURE_TENANT_ID",
    "AZURE-CLIENT-ID":                      "AZURE_CLIENT_ID",
    "AZURE-CLIENT-SECRET":                  "AZURE_CLIENT_SECRET",
    # ── Databricks ───────────────────────────────────────────────────────────
    "DATABRICKS-HOST":                      "DATABRICKS_HOST",
    "DATABRICKS-CATALOG":                   "DATABRICKS_CATALOG",
    "DATABRICKS-SCHEMA-NAME":               "DATABRICKS_SCHEMA_NAME",
    "DATABRICKS-VECTOR-SEARCH-ENDPOINT":    "DATABRICKS_VECTOR_SEARCH_ENDPOINT",
    "DATABRICKS-WAREHOUSE-ID":              "DATABRICKS_WAREHOUSE_ID",
    "DATABRICKS-CLIENT-ID":                 "DATABRICKS_CLIENT_ID",
    "DATABRICKS-CLIENT-SECRET":             "DATABRICKS_CLIENT_SECRET",
    # ── Observability ────────────────────────────────────────────────────────
    "LAKEBASE-INSTANCE-NAME":               "LAKEBASE_INSTANCE_NAME",
    "OBS-ENABLED":                          "OBS_ENABLED",
    # ── Azure Document Intelligence (OCR) ────────────────────────────────────
    "AZURE-DOCUMENT-INTELLIGENCE-ENDPOINT": "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    "AZURE-DOCUMENT-INTELLIGENCE-KEY":      "AZURE_DOCUMENT_INTELLIGENCE_KEY",
}

_config_loaded: bool = False


def _load_local_env_file() -> None:
    """Load variables from a local .env file, if present. override=False so values injected by the
    environment (e.g. an AKS ConfigMap) are not overwritten."""
    load_dotenv(override=False)
    logger.debug("Local .env file loaded, if present.")


def _key_vault_enabled() -> bool:
    """Key Vault is ON by default; set KEY_VAULT_ENABLED=false for pure .env local development."""
    return os.getenv("KEY_VAULT_ENABLED", "true").strip().lower() not in ("false", "0", "no")


def _key_vault_strict() -> bool:
    """When true, a Key Vault failure aborts startup (recommended for prod). Default FALSE: the app
    falls back to .env so it still runs when the vault is not yet accessible. Set KEY_VAULT_STRICT=true
    once the vault + credentials are wired up."""
    return os.getenv("KEY_VAULT_STRICT", "false").strip().lower() in ("true", "1", "yes")


def _key_vault_url() -> str:
    """Resolve the vault URL: explicit AZURE_KEY_VAULT_URL, else from AZURE_KEY_VAULT_NAME, else default."""
    explicit = os.getenv("AZURE_KEY_VAULT_URL")
    if explicit:
        return explicit.strip().rstrip("/") + "/"
    name = os.getenv("AZURE_KEY_VAULT_NAME", _DEFAULT_KEY_VAULT_NAME).strip()
    return f"https://{name}.vault.azure.net/"


def _get_secret_client():
    """Create an Azure Key Vault SecretClient using DefaultAzureCredential. azure-identity /
    azure-keyvault-secrets are imported lazily so pure .env dev (KEY_VAULT_ENABLED=false) needs neither."""
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    return SecretClient(vault_url=_key_vault_url(), credential=DefaultAzureCredential())


def _load_secrets_from_key_vault() -> None:
    """Fetch the mapped secrets and inject them into os.environ (vault is authoritative — it overrides
    any .env value for these keys). A missing/empty secret is logged and skipped; an auth/connectivity
    failure stops startup (fail fast rather than run with placeholder keys)."""
    from azure.core.exceptions import AzureError, ResourceNotFoundError

    secret_client = _get_secret_client()
    loaded = 0
    for secret_name, env_var_name in _KEY_VAULT_SECRET_MAPPING.items():
        try:
            secret = secret_client.get_secret(secret_name)
        except ResourceNotFoundError:
            logger.warning(
                "Secret '%s' not found in Key Vault — env var '%s' left as-is (.env value, if any).",
                secret_name, env_var_name,
            )
            continue
        except AzureError as exc:
            raise RuntimeError(
                f"Failed to retrieve secret '{secret_name}' (for '{env_var_name}') from Key Vault "
                f"{_key_vault_url()}."
            ) from exc

        if not secret.value:
            logger.warning("Secret '%s' has no value — env var '%s' not set.", secret_name, env_var_name)
            continue

        os.environ[env_var_name] = secret.value
        loaded += 1
        logger.debug("Loaded Key Vault secret '%s' into env var '%s'.", secret_name, env_var_name)

    logger.info("Loaded %d/%d secret(s) from Key Vault %s", loaded, len(_KEY_VAULT_SECRET_MAPPING),
                _key_vault_url())


def load_config(force_reload: bool = False) -> None:
    """Load local .env first, then overlay Azure Key Vault secrets. Call ONCE at startup, before any
    Settings()/get_settings() is instantiated.

    Args:
        force_reload: reload even if previously loaded (e.g. in tests).
    """
    global _config_loaded
    if _config_loaded and not force_reload:
        logger.debug("Configuration already loaded — skipping reload.")
        return

    _load_local_env_file()
    kv_used = False
    if _key_vault_enabled():
        try:
            _load_secrets_from_key_vault()
            kv_used = True
        except Exception as exc:  # noqa: BLE001 — incl. missing azure pkgs / auth / connectivity
            if _key_vault_strict():
                raise
            logger.warning(
                "Key Vault overlay skipped — continuing with .env / environment only "
                "(set KEY_VAULT_STRICT=true to make this fatal) | %s", exc,
            )
    else:
        logger.info("Key Vault disabled (KEY_VAULT_ENABLED=false) — using .env / environment only.")

    _config_loaded = True
    logger.info("Configuration loaded (.env%s).", " + Azure Key Vault" if kv_used else " only")
