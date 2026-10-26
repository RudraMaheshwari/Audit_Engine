"""FastAPI application factory.

The app is served from the project-root `main.py` (`uvicorn main:app` or `python main.py`). This module
only wires things together; routing, services, and error shaping live in their own modules (SRP).
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import build_api_router
from app.core.config import get_api_settings
from app.core.exceptions import register_exception_handlers
from src.utils.logger import get_logger

logger = get_logger("app.api")


def create_app() -> FastAPI:
    settings = get_api_settings()
    base = settings.normalized_base_path
    app = FastAPI(
        title=settings.title,
        version=settings.version,
        summary="AI Auditor — Risk Analysis, Scoping AI, Scoping Deck, and admin knowledge-file APIs.",
        # Serve the interactive docs under the external base path too (e.g. /cpr/risk-analysis/docs),
        # so the Swagger "Try it out" calls resolve against the mounted routes. Defaults to /docs when
        # no base path is set.
        docs_url=f"{base}/docs",
        redoc_url=f"{base}/redoc",
        openapi_url=f"{base}/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        """Log every HTTP request with method, path, status, and latency (errors logged with trace)."""
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            dur = (time.perf_counter() - start) * 1000
            logger.exception("[API] %s %s -> ERROR (%.0f ms)", request.method, request.url.path, dur)
            raise
        dur = (time.perf_counter() - start) * 1000
        logger.info(
            "[API] %s %s -> %d (%.0f ms)",
            request.method, request.url.path, response.status_code, dur,
        )
        return response

    register_exception_handlers(app)
    app.include_router(build_api_router())

    @app.get("/", tags=["health"], summary="Root")
    async def root() -> dict:
        return {"service": settings.title, "version": settings.version, "docs": f"{base}/docs"}

    return app
