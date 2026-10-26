"""Admin CRUD endpoints for the three global knowledge sources, with vector-index synchronisation.

  GET    /admin/{resource}/files            list files in the source
  GET    /admin/{resource}/files/{name}     download a file
  POST   /admin/{resource}/files            upload (multipart) -> write + ingest into the index
  DELETE /admin/{resource}/files/{name}     delete -> remove file + delete its chunks from the index

`resource` is one of: global-policies | global-scope-policies | fcpa-guide. The service runs blocking
volume I/O + ingestion in a threadpool so the event loop is never blocked.
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.schemas.admin import DeleteResult, FileListResponse, IngestResult
from app.services.admin_files import AdminFileService

router = APIRouter(prefix="/admin", tags=["Admin — Knowledge Files"])


@router.get("/{resource}/files", response_model=FileListResponse, summary="List files")
async def list_files(resource: str) -> FileListResponse:
    return await run_in_threadpool(AdminFileService().list_files, resource)


@router.get("/{resource}/files/{name}", summary="Download a file")
async def download_file(resource: str, name: str) -> Response:
    data, filename = await run_in_threadpool(AdminFileService().download_file, resource, name)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{resource}/files", response_model=IngestResult, summary="Upload a file (and ingest)")
async def upload_file(resource: str, file: UploadFile) -> IngestResult:
    content = await file.read()
    return await run_in_threadpool(
        AdminFileService().upload_file, resource, file.filename or "upload", content
    )


@router.delete("/{resource}/files/{name}", response_model=DeleteResult, summary="Delete a file (and de-index)")
async def delete_file(resource: str, name: str) -> DeleteResult:
    return await run_in_threadpool(AdminFileService().delete_file, resource, name)
