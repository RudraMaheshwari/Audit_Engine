"""API-layer configuration (separate from the engine's src.config.settings).

Only HTTP/service concerns live here — host, port, title, CORS, run retention. Engine behaviour
(Databricks, Azure, auditor, tables) stays in src.config.settings, which the services read directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """HTTP/service settings, overridable via environment (prefix API_) or .env."""

    model_config = SettingsConfigDict(env_prefix="API_", env_file=".env", extra="ignore")

    title: str = "AI Auditor API"
    version: str = "0.1.0"
    # URL version/segment inserted BELOW the base path. Empty by default — the scheme is
    # /cpr/risk-analysis/<api>/... with no version segment. Set API_PREFIX="/api/v1" to re-add one.
    api_prefix: str = ""

    # External base path this service is mounted under — e.g. behind the ingress at
    # https://gaa.audit.dev.jnj.com/cpr/risk-analysis . All API routes AND the docs are served beneath
    # it, so it also works on localhost (http://localhost:8000/cpr/risk-analysis/...). Set
    # API_BASE_PATH="" if your ingress STRIPS the prefix before forwarding to the pod.
    base_path: str = "/cpr/risk-analysis"

    host: str = "0.0.0.0"
    port: int = 8000

    # Comma-separated list of allowed CORS origins ("*" to allow all).
    cors_origins: str = "*"

    # How many finished runs to keep in memory before evicting the oldest.
    max_retained_runs: int = Field(default=200, ge=1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()] or ["*"]

    @property
    def normalized_base_path(self) -> str:
        """Leading-slash, no-trailing-slash base path ('/cpr/risk-analysis'), or '' when unset."""
        bp = (self.base_path or "").strip().strip("/")
        return f"/{bp}" if bp else ""


@lru_cache
def get_api_settings() -> ApiSettings:
    """Cached singleton so settings are parsed once per process."""
    return ApiSettings()
