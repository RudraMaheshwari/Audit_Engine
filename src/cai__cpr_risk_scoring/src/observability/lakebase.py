"""Lakebase Postgres connection factory.

Resolves the instance host and mints a short-lived database credential via the Databricks SDK
(the same method verified working against the `cbt-gaa-audit-dev` instance), then opens a psycopg
connection. Credentials are minted fresh on each connect so a reconnect during a long run always
uses a valid, unexpired token. Driver and SDK are imported lazily so importing this module never
fails when observability is turned off.
"""

from __future__ import annotations

import uuid

from src.config.constants import OBS_CONNECT_TIMEOUT_S
from src.config.settings import Settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class LakebaseConnectionError(RuntimeError):
    """Raised when a Lakebase connection cannot be established."""


class LakebaseConnection:
    """Opens psycopg connections to a Lakebase Postgres instance using Databricks auth."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @staticmethod
    def driver_available() -> bool:
        """True when the psycopg driver is importable."""
        try:
            import psycopg  # noqa: F401
            return True
        except ImportError:
            return False

    def _resolve_host_and_credential(self) -> tuple[str, str, str]:
        """Return (host, user, token) for the configured Lakebase instance."""
        instance_name = (self._settings.lakebase_instance_name or "").strip()
        if not instance_name:
            raise LakebaseConnectionError(
                "lakebase_instance_name is not set — cannot resolve the Lakebase host."
            )

        from databricks.sdk import WorkspaceClient

        from src.config.databricks_auth import build_config

        client = WorkspaceClient(config=build_config(self._settings))
        instance = client.database.get_database_instance(name=instance_name)
        host = instance.read_write_dns
        if not host:
            raise LakebaseConnectionError(
                f"Lakebase instance '{instance_name}' has no read_write_dns (is it running?)."
            )

        credential = client.database.generate_database_credential(
            request_id=str(uuid.uuid4()),
            instance_names=[instance_name],
        )
        user = (self._settings.lakebase_user or "").strip() or client.current_user.me().user_name
        return host, user, credential.token

    def open(self):
        """Open and return a live psycopg connection (caller owns commit/close)."""
        import psycopg

        host, user, token = self._resolve_host_and_credential()
        conn = psycopg.connect(
            host=host,
            port=self._settings.lakebase_port,
            dbname=self._settings.lakebase_database,
            user=user,
            password=token,
            sslmode=self._settings.lakebase_sslmode,
            connect_timeout=OBS_CONNECT_TIMEOUT_S,
            autocommit=False,
        )
        logger.info("[Obs] Lakebase connection open | host=%s | db=%s | user=%s",
                    host, self._settings.lakebase_database, user)
        return conn
