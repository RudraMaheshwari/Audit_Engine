"""Factory that builds the identical run-management endpoints for each API.

Every API exposes the same shape — start a run, poll its status, stream live progress (SSE), list runs
— so it's defined once here and instantiated per API with its own job function. Keeps the three
feature routers DRY while each stays an independent, clearly-tagged resource.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.config import get_api_settings
from app.core.exceptions import NotFoundError
from app.core.run_store import get_run_store
from app.schemas.runs import RunAccepted, RunState, RunStatus, StartRunRequest
from app.schemas.status_codes import ApiName
from app.services.run_manager import JobFn, get_run_manager

RequestModel = type[StartRunRequest]

_STREAM_POLL_SECONDS = 0.5


def _slug(api: ApiName) -> str:
    return api.value.replace("_", "-")


async def _event_stream(run_id: str, api: ApiName):
    """Yield an SSE event whenever the run's state changes, until it reaches a terminal state."""
    store = get_run_store()
    last_version = -1
    while True:
        record = store.get(run_id)
        if record is None or record.api != api:
            yield 'event: error\ndata: {"message": "run not found"}\n\n'
            return
        if record.version != last_version:
            last_version = record.version
            yield f"data: {record.to_status().model_dump_json()}\n\n"
        if record.is_terminal():
            return
        await asyncio.sleep(_STREAM_POLL_SECONDS)


def build_run_router(api: ApiName, job: JobFn, request_model: RequestModel = StartRunRequest) -> APIRouter:
    router = APIRouter()
    # Full external path to THIS api's routes, so the status_url returned in the 202 matches where the
    # routes are actually mounted (base path + optional version + api slug).
    settings = get_api_settings()
    base = f"{settings.normalized_base_path}{settings.api_prefix}/{_slug(api)}"

    async def start_run(request) -> RunAccepted:
        run_id = get_run_manager().start(api, request, job)
        return RunAccepted(
            run_id=run_id, api=api, state=RunState.PENDING,
            status_url=f"{base}/runs/{run_id}",
        )

    # Bind the per-API request model as a real type (not a lazy string) so FastAPI validates the body
    # against it — each API can require its own fields (e.g. Risk Analysis's mandatory inputs).
    start_run.__annotations__["request"] = request_model
    router.add_api_route(
        "/runs", start_run, methods=["POST"],
        response_model=RunAccepted, status_code=202, summary="Start a run",
    )

    @router.get("/runs", response_model=list[RunStatus], summary="List runs")
    async def list_runs() -> list[RunStatus]:
        return [r.to_status() for r in get_run_store().list(api)]

    @router.get("/runs/{run_id}", response_model=RunStatus, summary="Get run status")
    async def get_run(run_id: str) -> RunStatus:
        record = get_run_store().get(run_id)
        if record is None or record.api != api:
            raise NotFoundError("Run not found.", details={"run_id": run_id})
        return record.to_status()

    @router.get("/runs/{run_id}/stream", summary="Stream run progress (SSE)")
    async def stream_run(run_id: str) -> StreamingResponse:
        if get_run_store().get(run_id) is None:
            raise NotFoundError("Run not found.", details={"run_id": run_id})
        return StreamingResponse(_event_stream(run_id, api), media_type="text/event-stream")

    return router
