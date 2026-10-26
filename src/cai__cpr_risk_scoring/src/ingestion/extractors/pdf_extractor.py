from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf as fitz
import pdfplumber
from PIL import Image

from src.config.constants import PDF_MIN_TEXT_CHARS, PDF_OCR_DPI, PDF_PROGRESS_LOG_EVERY
from src.ingestion.extractors.base import BaseExtractor
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.models.schemas import ExtractedDocument
from src.utils.logger import get_logger, log_extraction_summary

logger = get_logger(__name__)

_OCR_MATRIX = fitz.Matrix(PDF_OCR_DPI / 72, PDF_OCR_DPI / 72)
_PAGE_OCR_WORKERS = 4


class PdfExtractor(BaseExtractor):
    def __init__(self, ocr_provider: BaseOCRProvider) -> None:
        self._ocr = ocr_provider

    @property
    def supported_extensions(self) -> set[str]:
        return {"pdf"}

    def extract(self, file_path: Path, scope_id: str) -> ExtractedDocument:
        fname = file_path.name
        ocr_name = type(self._ocr).__name__

        pdfplumber_texts = self._extract_text_layers(file_path)
        fitz_doc = fitz.open(str(file_path))
        page_count = len(fitz_doc)

        logger.info(
            "[PDF] ▶ START  file='%s'  pages=%d  ocr_provider=%s  scope=%s",
            fname, page_count, ocr_name, scope_id,
        )

        pages: list[str | None] = [None] * page_count
        text_pages = 0
        ocr_needed: list[int] = []  # 1-based

        # Step 1 — pdfplumber text layer (unchanged)
        for i, plumber_text in enumerate(pdfplumber_texts, start=1):
            if plumber_text:
                pages[i - 1] = plumber_text
                text_pages += 1
                logger.debug(
                    "[PDF] page=%d/%d  mode=text-layer  chars=%d  file='%s'",
                    i, page_count, len(plumber_text), fname,
                )
            else:
                ocr_needed.append(i)

        # Step 2 — per-page image OCR, parallelised
        ocr_pages = 0
        empty_after_ocr: list[int] = []

        if ocr_needed:
            logger.debug(
                "[PDF] %d pages need image OCR  file='%s'", len(ocr_needed), fname,
            )

            def _ocr_page(page_num: int) -> tuple[int, str]:
                fitz_page = fitz_doc[page_num - 1]
                pixmap = fitz_page.get_pixmap(matrix=_OCR_MATRIX)
                image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
                return page_num, self._ocr.image_to_text(image)

            with ThreadPoolExecutor(max_workers=_PAGE_OCR_WORKERS) as pool:
                futures = {pool.submit(_ocr_page, pn): pn for pn in ocr_needed}
                done = 0
                for future in as_completed(futures):
                    page_num, ocr_text = future.result()
                    done += 1
                    if page_count > PDF_PROGRESS_LOG_EVERY and done % PDF_PROGRESS_LOG_EVERY == 1:
                        logger.info(
                            "[PDF] Processing  file='%s'  page=%d/%d  text_so_far=%d  ocr_so_far=%d",
                            fname, page_num, page_count, text_pages, ocr_pages,
                        )
                    if ocr_text:
                        pages[page_num - 1] = ocr_text
                        ocr_pages += 1
                        logger.debug(
                            "[PDF] page=%d/%d  mode=OCR  chars=%d  file='%s'",
                            page_num, page_count, len(ocr_text), fname,
                        )
                    else:
                        empty_after_ocr.append(page_num)
                        logger.warning(
                            "[PDF] page=%d/%d  mode=OCR  result=EMPTY  file='%s'",
                            page_num, page_count, fname,
                        )

        fitz_doc.close()

        # Step 3 — pdf_to_text() fallback for pages still empty (1 Azure DI call)
        if empty_after_ocr and hasattr(self._ocr, "pdf_to_text"):
            logger.info(
                "[PDF] %d pages still empty — trying pdf_to_text() fallback | file='%s'",
                len(empty_after_ocr), fname,
            )
            try:
                fallback_texts = self._ocr.pdf_to_text(file_path)
                recovered = 0
                still_empty: list[int] = []
                for pn in empty_after_ocr:
                    if pn <= len(fallback_texts) and fallback_texts[pn - 1]:
                        pages[pn - 1] = fallback_texts[pn - 1]
                        ocr_pages += 1
                        recovered += 1
                    else:
                        still_empty.append(pn)
                logger.info(
                    "[PDF] pdf_to_text fallback recovered %d/%d pages | file='%s'",
                    recovered, len(empty_after_ocr), fname,
                )
                empty_after_ocr = still_empty
            except Exception as exc:
                logger.warning(
                    "[PDF] pdf_to_text fallback failed | error=%s | file='%s'", exc, fname,
                )

        tagged = [f"[PAGE:{i + 1}]\n\n{text}" for i, text in enumerate(pages) if text]
        content = "\n\n".join(tagged)

        logger.info(
            "[PDF] ✔ DONE  file='%s'  pages_total=%d  pages_text=%d  pages_ocr=%d  "
            "pages_empty=%d  content_chars=%d",
            fname, page_count, text_pages, ocr_pages, len(empty_after_ocr), len(content),
        )
        if empty_after_ocr:
            logger.warning(
                "[PDF] Empty pages after all extraction attempts  file='%s'  page_numbers=%s",
                fname, empty_after_ocr[:20],
            )

        log_extraction_summary(
            logger, fname, "PDF", len(content),
            extra={
                "pages_total":      page_count,
                "pages_text_layer": text_pages,
                "pages_ocr":        ocr_pages,
                "pages_empty":      len(empty_after_ocr),
            },
        )

        return ExtractedDocument(
            file_path=str(file_path),
            file_name=fname,
            file_type="pdf",
            content=content,
            scope_id=scope_id,
            page_count=page_count,
            metadata={
                "extractor":        "pdfplumber+ocr",
                "ocr_provider":     ocr_name,
                "pages_text_layer": text_pages,
                "pages_ocr":        ocr_pages,
                "empty_pages":      empty_after_ocr,
            },
        )

    def _extract_text_layers(self, file_path: Path) -> list[str | None]:
        results: list[str | None] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text and len(text.strip()) >= PDF_MIN_TEXT_CHARS:
                    results.append(text.strip())
                else:
                    results.append(None)
        return results
