"""Liveness/readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter()


@router.get("/health", summary="Health check", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "version": __version__}
