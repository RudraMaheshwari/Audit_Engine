import io
import zipfile
from pathlib import Path

from docx import Document
from PIL import Image

from src.config.constants import OCR_SUPPORTED_IMAGE_FORMATS
from src.ingestion.extractors.base import BaseExtractor
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.models.schemas import ExtractedDocument
from src.utils.logger import get_logger, log_extraction_summary

logger = get_logger(__name__)

_MIN_IMAGE_BYTES = 5_000


class WordExtractor(BaseExtractor):
    """
    Extracts paragraphs, table cells, and inline images from DOCX/DOC files.
    File is opened from binary bytes (rb) to ensure format portability.
    Embedded images are OCR-d via the injected OCR provider.
    """

    def __init__(self, ocr_provider: BaseOCRProvider) -> None:
        self._ocr = ocr_provider

    @property
    def supported_extensions(self) -> set[str]:
        return {"docx", "doc"}

    def extract(self, file_path: Path, scope_id: str) -> ExtractedDocument:
        fname = file_path.name
        logger.info("[Word] ▶ START  file='%s'  scope=%s", fname, scope_id)

        raw_bytes = file_path.read_bytes()
        try:
            doc = Document(io.BytesIO(raw_bytes))
        except zipfile.BadZipFile:
            logger.warning("[Word] Not a valid docx (legacy binary .doc or corrupted) — skipping  file='%s'", fname)
            return ExtractedDocument(file_path=str(file_path), file_name=fname, file_type="docx", content="", scope_id=scope_id, metadata={"extractor": "python-docx+ocr", "error": "not_a_zip_file"})

        parts: list[str] = []
        para_count = 0
        table_row_count = 0
        images_ocr = 0

        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)
                para_count += 1

        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
                    table_row_count += 1

        for rel in doc.part.rels.values():
            if "image" not in rel.reltype:
                continue
            try:
                img_bytes = rel.target_part.blob
                if len(img_bytes) < _MIN_IMAGE_BYTES:
                    logger.debug("[Word] Skipping small image  bytes=%d  file='%s'", len(img_bytes), fname)
                    continue

                # Validate PIL can open it and format is supported before sending to OCR
                try:
                    probe = Image.open(io.BytesIO(img_bytes))
                    fmt = probe.format  # e.g. "WMF", "EMF", "PNG", "JPEG"
                    probe.close()
                except Exception:
                    logger.debug("[Word] Unreadable image format — skipping  file='%s'", fname)
                    continue

                if fmt not in OCR_SUPPORTED_IMAGE_FORMATS:
                    logger.debug("[Word] Unsupported image format=%s — skipping  file='%s'", fmt, fname)
                    continue

                pil_image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                logger.debug("[Word] Running OCR on embedded image  fmt=%s  bytes=%d  file='%s'", fmt, len(img_bytes), fname)
                ocr_text = self._ocr.image_to_text(pil_image)
                if ocr_text:
                    parts.append(f"[Embedded Image OCR]\n{ocr_text}")
                    images_ocr += 1
                    logger.debug("[Word] Image OCR done  chars=%d  file='%s'", len(ocr_text), fname)
            except Exception as exc:
                logger.warning("[Word] Image OCR failed  error=%s  file='%s'", exc, fname)

        content = "\n\n".join(parts)

        logger.info(
            "[Word] ✔ DONE  file='%s'  paragraphs=%d  tables=%d  table_rows=%d  "
            "images_ocr=%d  content_chars=%d",
            fname, para_count, len(doc.tables), table_row_count, images_ocr, len(content),
        )
        log_extraction_summary(
            logger, fname, "Word", len(content),
            extra={
                "paragraphs":  para_count,
                "tables":      len(doc.tables),
                "table_rows":  table_row_count,
                "images_ocr":  images_ocr,
            },
        )

        return ExtractedDocument(
            file_path=str(file_path),
            file_name=fname,
            file_type="docx",
            content=content,
            scope_id=scope_id,
            metadata={
                "extractor":   "python-docx+ocr",
                "paragraphs":  para_count,
                "tables":      len(doc.tables),
                "table_rows":  table_row_count,
                "images_ocr":  images_ocr,
            },
        )
