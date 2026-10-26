"""Per-auditor supporting-document upload + ingest.

  POST /supporting-docs/files   multipart: auditor_id (form) + file -> write to the auditor's
                                supporting-docs volume folder AND (re)ingest into the `supporting`
                                index, tagged with that auditor_id.

Blocking volume I/O + ingestion runs in a threadpool so the event loop is never blocked.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.schemas.admin import SupportingDocIngestResult
from app.services.supporting_ingest import SupportingDocIngestService

router = APIRouter(prefix="/supporting-docs", tags=["Supporting Docs — Ingest"])


@router.post("/files", response_model=SupportingDocIngestResult,
             summary="Upload a supporting document for an auditor (and ingest)")
async def upload_file(
    auditor_id: str = Form(...),
    file: UploadFile = File(...),
) -> SupportingDocIngestResult:
    content = await file.read()
    return await run_in_threadpool(
        SupportingDocIngestService().upload_file, auditor_id, file.filename or "upload", content
    )
