"""Central Databricks auth — Service Principal OAuth (M2M). Replaces PAT (`databricks_token`).

Everything that talks to Databricks (SQLAlchemy engines, the SQL warehouse, Vector Search, Volumes REST,
Lakebase) builds its credentials here, so there is ONE place that knows how we authenticate.

Auth = a Databricks service principal's OAuth client_id/client_secret (settings.databricks_client_id /
databricks_client_secret). The SDK's credentials provider mints and auto-refreshes short-lived bearer
tokens, so engines stay valid across long runs.
"""

from __future__ import annotations

from databricks import sql
from databricks.sdk.core import Config, oauth_service_principal
from sqlalchemy import create_engine

from src.config.constants import DATABRICKS_WAREHOUSE_PATH_PREFIX


def _host(settings) -> str:
    return settings.databricks_host.replace("https://", "").replace("http://", "").rstrip("/")


def has_credentials(settings) -> bool:
    """True when the service-principal OAuth credentials are configured."""
    return bool(
        getattr(settings, "databricks_client_id", "")
        and getattr(settings, "databricks_client_secret", "")
    )


def build_config(settings) -> Config:
    """SDK Config authenticated as the service principal (OAuth M2M)."""
    return Config(
        host=f"https://{_host(settings)}",
        client_id=settings.databricks_client_id,
        client_secret=settings.databricks_client_secret,
        auth_type="oauth-m2m",
    )


def _credentials_provider(settings):
    """Factory the databricks-sql connector expects: a callable returning the OAuth header provider."""
    cfg = build_config(settings)
    return lambda: oauth_service_principal(cfg)


def bearer_token(settings) -> str:
    """Mint a fresh SP OAuth bearer token for clients that need a raw token string (Volumes REST,
    Lakebase credential bootstrap). Short-lived — call it at the point of use, do not cache."""
    headers = oauth_service_principal(build_config(settings))()
    return headers["Authorization"].split(" ", 1)[1]


def build_engine(settings, *, schema: str | None = None, **engine_kwargs):
    """A SQLAlchemy engine for the configured SQL warehouse, authenticated via SP OAuth.

    Uses a `creator` that opens each DBAPI connection through the connector's `credentials_provider`,
    so tokens refresh automatically (no expiry mid-run) and there is no PAT in any connection string.
    `schema` is optional (omit to leave the session default — callers that fully-qualify names don't
    need it). Extra engine_kwargs (pool_size, max_overflow, pool_pre_ping, …) pass straight through.
    """
    host = _host(settings)
    http_path = f"{DATABRICKS_WAREHOUSE_PATH_PREFIX}{settings.databricks_warehouse_id}"
    catalog = settings.databricks_catalog
    provider = _credentials_provider(settings)

    def _creator():
        kwargs = {
            "server_hostname": host,
            "http_path": http_path,
            "catalog": catalog,
            "credentials_provider": provider,
        }
        if schema:
            kwargs["schema"] = schema
        return sql.connect(**kwargs)

    # The `creator` supplies the real (OAuth) connection, but the databricks SQLAlchemy DIALECT reads
    # catalog/schema from the URL (not the creator). Without them, self.catalog is None and reflection
    # (get_table_names -> ".".join([self.catalog, schema])) raises "expected str, NoneType found". So
    # pass catalog (and schema, when set) in the URL query to populate the dialect.
    from urllib.parse import quote_plus

    query = f"catalog={quote_plus(str(catalog))}" if catalog else ""
    if schema:
        query = f"{query}&schema={quote_plus(str(schema))}" if query else f"schema={quote_plus(str(schema))}"
    url = f"databricks://{host}?{query}" if query else f"databricks://{host}"
    return create_engine(url, creator=_creator, **engine_kwargs)
