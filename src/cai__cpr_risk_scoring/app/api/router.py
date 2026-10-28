"""Aggregates all routers under the API prefix — the one place new APIs get registered."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import supporting_router
from app.api.run_router import build_run_router
from app.core.config import get_api_settings
from app.schemas.runs import (
    RiskAnalysisStartRequest,
    ScopingAIStartRequest,
    ScopingDeckStartRequest,
)
from app.schemas.status_codes import ApiName
from app.services import scoping_deck
from app.api import admin_router, health
from app.services import risk_analysis, scoping_ai


def build_api_router() -> APIRouter:
    settings = get_api_settings()
    # Mount the APIs under the external base path (e.g. /cpr/risk-analysis) + the version prefix, so the
    # full path is e.g. /cpr/risk-analysis/api/v1/risk-analysis. `health` stays at the root (/health) so
    # infra liveness/readiness probes hit the pod directly, independent of the external mount.
    api_prefix = f"{settings.normalized_base_path}{settings.api_prefix}"
    router = APIRouter()
    # Health is served at the root (/health) for infra liveness/readiness probes AND under the external
    # base path (e.g. /cpr/risk-analysis/health) for callers reaching the service through the mount. The
    # guard avoids registering /health twice when the prefix is empty.
    router.include_router(health.router)
    if api_prefix:
        router.include_router(health.router, prefix=api_prefix)
    router.include_router(admin_router.router, prefix=api_prefix)
    router.include_router(supporting_router.router, prefix=api_prefix)

    router.include_router(
        build_run_router(ApiName.RISK_ANALYSIS, risk_analysis.run, RiskAnalysisStartRequest),
        prefix=f"{api_prefix}/risk-analysis", tags=["Risk Analysis"],
    )
    router.include_router(
        build_run_router(ApiName.SCOPING_AI, scoping_ai.run, ScopingAIStartRequest),
        prefix=f"{api_prefix}/scoping-ai", tags=["Scoping AI"],
    )
    router.include_router(
        build_run_router(ApiName.SCOPING_DECK, scoping_deck.run, ScopingDeckStartRequest),
        prefix=f"{api_prefix}/scoping-deck", tags=["Scoping Deck"],
    )
    return router
