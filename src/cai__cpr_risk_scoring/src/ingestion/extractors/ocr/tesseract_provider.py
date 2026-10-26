from pathlib import Path

import pymupdf as fitz  # PyMuPDF — converts PDF pages to images
import pytesseract
from PIL import Image

from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DPI = 300
_MATRIX = fitz.Matrix(_DPI / 72, _DPI / 72)


class TesseractOCRProvider(BaseOCRProvider):
    """
    Local OCR using Tesseract + PyMuPDF.
    Use for dev/testing or when Azure Document Intelligence is not available.
    Requires: tesseract binary installed + pytesseract + PyMuPDF (fitz) + Pillow.
    """

    def __init__(self, lang: str = "eng+jpn", tesseract_cmd: str | None = None) -> None:
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self._lang = lang
        logger.info("[TesseractOCR] Initialised | lang=%s", lang)

    def image_to_text(self, image: Image.Image) -> str:
        text = pytesseract.image_to_string(image, lang=self._lang)
        return text.strip()

    def pdf_to_text(self, file_path: Path) -> list[str]:
        logger.info("[TesseractOCR] Processing PDF | file=%s", file_path.name)
        doc = fitz.open(str(file_path))
        page_texts: list[str] = []

        for page_num, page in enumerate(doc, start=1):
            pixmap = page.get_pixmap(matrix=_MATRIX)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            text = self.image_to_text(image)
            page_texts.append(text)
            logger.debug("[TesseractOCR] Page %d | chars=%d", page_num, len(text))

        doc.close()
        logger.info("[TesseractOCR] Done | pages=%d | file=%s", len(page_texts), file_path.name)
        return page_texts
