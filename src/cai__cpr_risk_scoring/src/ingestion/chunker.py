from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional

import tiktoken

from src.config.constants import SECTION_HEADER_MARKERS
from src.ingestion.metadata_extractor import MetadataExtractor
from src.models.schemas import DocumentChunk, DocumentSource, ExtractedDocument
from src.schemas.state import ChunkMetadata, DocumentType
from src.utils.logger import get_logger, log_chunk_summary

logger = get_logger(__name__)

_PAGE_MARKER_RE = re.compile(r"^\[PAGE:(\d+)\]$")

_meta_extractor = MetadataExtractor()


class Chunker:
    """
    Section-aware, token-counted chunker. Splits document content on recognised
    section headers first; oversized sections are hard-split on paragraph boundaries.
    Produces DocumentChunks with ChunkMetadata populated from the surrounding section context.
    """

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        encoding_name: str = "cl100k_base",
    ) -> None:
        if chunk_size <= chunk_overlap:
            raise ValueError("chunk_size must be greater than chunk_overlap")
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._enc = tiktoken.get_encoding(encoding_name)
        logger.info(
            "[Chunker] Initialised | strategy=section-aware | chunk_size=%d | overlap=%d | encoding=%s",
            chunk_size, chunk_overlap, encoding_name,
        )

    def chunk(self, document: ExtractedDocument, source: DocumentSource) -> list[DocumentChunk]:
        """Chunk a single ExtractedDocument into DocumentChunks ready for vector indexing."""
        logger.info(
            "[Chunker] Starting | file=%s | source=%s | content_chars=%d",
            document.file_name, source.value, len(document.content),
        )

        paragraphs = self._split_into_paragraphs(document.content)
        logger.debug("[Chunker] Paragraphs detected=%d | file=%s", len(paragraphs), document.file_name)

        chunks: list[DocumentChunk] = []
        buffer: list[str] = []
        buffer_tokens = 0
        current_header:        Optional[str] = None
        current_section_no:    Optional[str] = None
        current_section_title: Optional[str] = None
        current_page:          Optional[int] = None
        sections_detected: list[str] = []
        hard_splits = 0

        for para in paragraphs:
            m_page = _PAGE_MARKER_RE.match(para)
            if m_page:
                current_page = int(m_page.group(1))
                continue

            if self._is_section_header(para):
                current_header = para
                current_section_no, current_section_title = _meta_extractor.parse_section_header(para)
                sections_detected.append(para[:60])
                logger.debug(
                    "[Chunker] Section | no=%s | title=%s | file=%s",
                    current_section_no, current_section_title[:40] if current_section_title else "", document.file_name,
                )

            para_tokens = len(self._enc.encode(para))

            if para_tokens > self._chunk_size:
                if buffer:
                    chunks.append(self._build_chunk(
                        document, source, buffer, current_header,
                        current_section_no, current_section_title, current_page,
                    ))
                    buffer, buffer_tokens = self._overlap_buffer(buffer)
                for sub in self._hard_split(para):
                    chunks.append(self._build_chunk(
                        document, source, [sub], current_header,
                        current_section_no, current_section_title, current_page,
                    ))
                    hard_splits += 1
                continue

            if buffer_tokens + para_tokens > self._chunk_size and buffer:
                chunks.append(self._build_chunk(
                    document, source, buffer, current_header,
                    current_section_no, current_section_title, current_page,
                ))
                buffer, buffer_tokens = self._overlap_buffer(buffer)

            buffer.append(para)
            buffer_tokens += para_tokens

        if buffer:
            chunks.append(self._build_chunk(
                document, source, buffer, current_header,
                current_section_no, current_section_title, current_page,
            ))

        avg_tokens = sum(c.token_count for c in chunks) / len(chunks) if chunks else 0.0
        log_chunk_summary(
            logger, document.file_name,
            total_chunks=len(chunks), avg_tokens=avg_tokens,
            sections_detected=len(sections_detected),
            chunk_size=self._chunk_size, overlap=self._chunk_overlap,
        )
        if sections_detected:
            logger.info("[Chunker] Sections found in '%s': %s", document.file_name, sections_detected[:5])
        if hard_splits:
            logger.info("[Chunker] Hard splits applied=%d | file=%s", hard_splits, document.file_name)

        return chunks

    def _build_chunk(
        self,
        doc: ExtractedDocument,
        source: DocumentSource,
        buffer: list[str],
        header: Optional[str],
        section_no: Optional[str],
        section_title: Optional[str],
        page_number: Optional[int],
    ) -> DocumentChunk:
        prefix = f"[{header}]\n" if header else ""
        content = prefix + "\n\n".join(buffer)
        token_count = len(self._enc.encode(content))
        chunk_id = str(uuid.uuid4())

        doc_meta = doc.metadata

        dtype_val = doc_meta.get("document_type", "")
        try:
            dtype = DocumentType(dtype_val)
        except ValueError:
            dtype = DocumentType.SUPPORTING_DOC
            logger.debug("[Chunker] Unknown document_type '%s'; defaulting to SUPPORTING_DOC", dtype_val)

        chunk_meta = ChunkMetadata(
            chunk_id=chunk_id,
            document_name=doc.file_name,
            document_type=dtype,
            section_title=section_title,
            section_no=section_no,
            page_number=page_number,
            version=doc_meta.get("version"),
            effective_date=doc_meta.get("effective_date"),
            language=doc_meta.get("language", "English"),
            policy_domain=doc_meta.get("policy_domain"),
            policy_name=doc_meta.get("policy_name"),
            policy_number=doc_meta.get("policy_number"),
            country=doc_meta.get("country"),
            policy_owner=doc_meta.get("policy_owner"),
            index_name=doc_meta.get("index_name", ""),
            file_format=doc.file_type,
            ingestion_timestamp=datetime.utcnow(),
            parent_email=doc_meta.get("parent_email"),
        )

        return DocumentChunk(
            chunk_id=chunk_id,
            document_name=doc.file_name,
            source=source,
            content=content,
            token_count=token_count,
            scope_id=doc.scope_id,
            metadata=chunk_meta.model_dump(),
        )

    def _overlap_buffer(self, buffer: list[str]) -> tuple[list[str], int]:
        total = 0
        keep: list[str] = []
        for para in reversed(buffer):
            t = len(self._enc.encode(para))
            if total + t > self._chunk_overlap:
                break
            keep.insert(0, para)
            total += t
        return keep, total

    def _hard_split(self, text: str) -> list[str]:
        tokens = self._enc.encode(text)
        parts: list[str] = []
        step = self._chunk_size - self._chunk_overlap
        for i in range(0, len(tokens), step):
            part_tokens = tokens[i: i + self._chunk_size]
            parts.append(self._enc.decode(part_tokens))
        return parts

    @staticmethod
    def _split_into_paragraphs(text: str) -> list[str]:
        return [p.strip() for p in text.split("\n\n") if p.strip()]

    @staticmethod
    def _is_section_header(text: str) -> bool:
        upper = text.upper().strip()
        return (
            any(upper.startswith(m) for m in SECTION_HEADER_MARKERS)
            or (len(text) < 120 and text.isupper() and len(text.split()) > 1)
            or bool(re.match(r"^\d+(?:\.\d+)*[.\s]\s+[A-Z]", text))
        )
