"""Response models for the admin file-management APIs (global policies, scope-area policies, FCPA guide)."""

from __future__ import annotations

from pydantic import BaseModel


class FileInfo(BaseModel):
    name: str
    path: str
    size_bytes: int | None = None
    modified: str | None = None


class FileListResponse(BaseModel):
    resource: str
    index_key: str
    count: int
    files: list[FileInfo]


class IngestResult(BaseModel):
    resource: str
    file: str
    status: str                 # "ingested" | "replaced"
    chunks_indexed: int
    chunks_removed: int = 0     # prior chunks cleared (on replace / re-ingest)
    path: str


class DeleteResult(BaseModel):
    resource: str
    file: str
    deleted_from_volume: bool
    chunks_removed: int


class SupportingDocIngestResult(BaseModel):
    """Result of ingesting a UI-uploaded supporting document into an auditor's supporting-docs index."""
    auditor_id: str
    file: str
    index_key: str = "supporting"
    status: str                 # "ingested" | "replaced"
    chunks_indexed: int
    chunks_removed: int = 0      # prior chunks for this auditor's same-named file (on replace)
    path: str                   # volume path the file was written to
