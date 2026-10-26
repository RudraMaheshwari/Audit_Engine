import io
from pathlib import Path

import pymupdf as fitz
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from PIL import Image

from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DPI = 300
_MATRIX = fitz.Matrix(_DPI / 72, _DPI / 72)


class AzureDocumentIntelligenceOCRProvider(BaseOCRProvider):
    """
    OCR using Azure AI Document Intelligence (prebuilt-read layout model).
    Primary OCR provider for image-based PDFs in the JNJ environment.
    Requires: azure-ai-documentintelligence package.

    Two modes:
      - pdf_to_text(): submits the whole PDF file directly to Azure DI — most accurate,
        handles multi-page in one API call.
      - image_to_text(): converts a single PIL image to bytes and submits — used by PPT extractor
        for individual embedded slide images.
    """

    def __init__(self, endpoint: str, api_key: str) -> None:
        self._client = DocumentIntelligenceClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(api_key),
        )
        logger.info("[AzureDI] Initialised | endpoint=%s", endpoint)

    def image_to_text(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        try:
            poller = self._client.begin_analyze_document(
                "prebuilt-read",
                body=AnalyzeDocumentRequest(bytes_source=buf.read()),
                content_type="image/png",
            )
            result = poller.result()
        except HttpResponseError as exc:
            if "InvalidContent" in str(exc):
                logger.debug("[AzureDI] image_to_text | skipped corrupted/unsupported image bytes")
                return ""
            raise
        text = "\n".join(p.content for p in result.paragraphs or [])
        logger.debug("[AzureDI] image_to_text | chars=%d", len(text))
        return text.strip()

    def pdf_to_text(self, file_path: Path) -> list[str]:
        """
        Submit the full PDF to Azure DI. Azure DI processes all pages in one call.
        Returns a list of per-page text strings by splitting on page breaks in the result.
        """
        logger.info("[AzureDI] Processing PDF | file=%s", file_path.name)

        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        try:
            poller = self._client.begin_analyze_document(
                "prebuilt-read",
                body=AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                content_type="application/pdf",
            )
            result = poller.result()
        except HttpResponseError as exc:
            exc_str = str(exc)
            if "InvalidContent" in exc_str:
                logger.warning("[AzureDI] pdf_to_text | PDF rejected as invalid/corrupted | file=%s", file_path.name)
                return []
            if "UnsupportedContent" in exc_str:
                logger.debug("[AzureDI] pdf_to_text | PDF has encrypted/signed content Azure DI cannot read | file=%s", file_path.name)
                return []
            raise

        page_texts: dict[int, list[str]] = {}
        for para in result.paragraphs or []:
            for region in para.bounding_regions or []:
                page_no = region.page_number
                page_texts.setdefault(page_no, []).append(para.content)

        max_page = max(page_texts.keys(), default=0)
        ordered: list[str] = []
        for page_no in range(1, max_page + 1):
            text = "\n".join(page_texts.get(page_no, []))
            ordered.append(text)
            logger.debug("[AzureDI] Page %d | chars=%d", page_no, len(text))

        logger.info("[AzureDI] Done | pages=%d | file=%s", len(ordered), file_path.name)
        return ordered
