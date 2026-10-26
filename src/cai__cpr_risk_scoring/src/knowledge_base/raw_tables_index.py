"""Build indexable chunks describing the raw transactional table schemas.

The raw-transactional-tables index stores each dashboard table's column layout so the NL2SQL and
classifier steps have awareness of what tables/columns exist. It is a context/awareness layer only —
the actual arithmetic always runs against the live tables.
"""

import re

from src.models.schemas import DocumentChunk, DocumentSource

_CHUNK_PREFIX = "rawtbl"
_TABLE_SPLIT = re.compile(r"(?=^Table:\s)", re.MULTILINE)
_TABLE_NAME = re.compile(r"^Table:\s*(.+)$", re.MULTILINE)


def build_raw_table_chunks(table_info: str, auditor_id: str) -> list[DocumentChunk]:
    """Split the table-info text into one chunk per table for the raw-tables index."""
    if not table_info or not table_info.strip():
        return []

    chunks: list[DocumentChunk] = []
    blocks = [b.strip() for b in _TABLE_SPLIT.split(table_info) if b.strip()]
    for position, block in enumerate(blocks):
        name_match = _TABLE_NAME.search(block)
        table_name = name_match.group(1).strip() if name_match else f"table_{position}"
        chunks.append(
            DocumentChunk(
                chunk_id=f"{_CHUNK_PREFIX}::{auditor_id}::{position}",
                document_name=table_name,
                source=DocumentSource.RAW_TRANSACTIONAL,
                content=block,
                token_count=max(1, len(block.split())),
                scope_id=DocumentSource.RAW_TRANSACTIONAL.value,
                auditor_folder=auditor_id,
                metadata={"table_name": table_name},
            )
        )
    return chunks
