import io
import zipfile
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.config.constants import OCR_SUPPORTED_IMAGE_FORMATS
from src.ingestion.extractors.base import BaseExtractor
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.models.schemas import ExtractedDocument
from src.utils.logger import get_logger, log_extraction_summary

logger = get_logger(__name__)

_MIN_IMAGE_PIXELS = 10_000


class PptExtractor(BaseExtractor):
    """
    PPT/PPTX extractor with OCR for embedded images.
    Text frames are extracted via python-pptx.
    Picture shapes and embedded images that have no text are passed through the OCR provider.
    OCR provider is injected (DIP) — same abstract pattern as PdfExtractor.
    """

    def __init__(self, ocr_provider: BaseOCRProvider) -> None:
        self._ocr = ocr_provider

    @property
    def supported_extensions(self) -> set[str]:
        return {"pptx", "ppt"}

    def extract(self, file_path: Path, scope_id: str) -> ExtractedDocument:
        fname = file_path.name
        try:
            prs = Presentation(file_path)
        except zipfile.BadZipFile:
            logger.warning("[PPT] Not a valid pptx (legacy binary .ppt or corrupted) — skipping  file='%s'", fname)
            return ExtractedDocument(file_path=str(file_path), file_name=fname, file_type="pptx", content="", scope_id=scope_id, metadata={"extractor": "python-pptx+ocr", "error": "not_a_zip_file"})
        slide_count = len(prs.slides)
        logger.info("[PPT] ▶ START  file='%s'  slides=%d  scope=%s", fname, slide_count, scope_id)

        slides: list[str] = []
        empty_slides: list[int] = []
        total_text_lines = 0
        total_images_ocr = 0

        for idx, slide in enumerate(prs.slides, start=1):
            slide_parts: list[str] = []

            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        line = para.text.strip()
                        if line:
                            slide_parts.append(line)
                            total_text_lines += 1

                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    ocr_text = self._ocr_shape_image(shape, fname)
                    if ocr_text:
                        slide_parts.append(f"[Image OCR]\n{ocr_text}")
                        total_images_ocr += 1
                        logger.debug(
                            "[PPT] Image OCR done  slide=%d/%d  chars=%d  file='%s'",
                            idx, slide_count, len(ocr_text), fname,
                        )

            if slide_parts:
                slides.append(f"[Slide {idx}]\n" + "\n".join(slide_parts))
                logger.debug(
                    "[PPT] slide=%d/%d  text_lines=%d  file='%s'",
                    idx, slide_count, len(slide_parts), fname,
                )
            else:
                empty_slides.append(idx)
                logger.debug("[PPT] slide=%d/%d  result=EMPTY  file='%s'", idx, slide_count, fname)

        if empty_slides:
            logger.debug(
                "[PPT] Empty slides  count=%d  slides=%s  file='%s'",
                len(empty_slides), empty_slides[:10], fname,
            )

        content = "\n\n".join(slides)
        logger.info(
            "[PPT] ✔ DONE  file='%s'  slides_total=%d  slides_with_text=%d  "
            "empty=%d  text_lines=%d  images_ocr=%d  content_chars=%d",
            fname, slide_count, len(slides), len(empty_slides),
            total_text_lines, total_images_ocr, len(content),
        )
        log_extraction_summary(
            logger, fname, "PPT", len(content),
            extra={
                "slides_total":     slide_count,
                "slides_with_text": len(slides),
                "empty_slides":     len(empty_slides),
                "text_lines":       total_text_lines,
                "images_ocr":       total_images_ocr,
            },
        )

        return ExtractedDocument(
            file_path=str(file_path),
            file_name=fname,
            file_type="pptx",
            content=content,
            scope_id=scope_id,
            page_count=slide_count,
            metadata={
                "extractor":        "python-pptx+ocr",
                "slides_total":     slide_count,
                "slides_with_text": len(slides),
                "empty_slides":     empty_slides,
                "images_ocr":       total_images_ocr,
            },
        )

    def _ocr_shape_image(self, shape, fname: str = "") -> str:
        """Extract and OCR an embedded picture shape. Returns empty string if too small or unsupported."""
        try:
            image_bytes = shape.image.blob
            probe = Image.open(io.BytesIO(image_bytes))
            fmt = probe.format
            probe.close()
            if fmt not in OCR_SUPPORTED_IMAGE_FORMATS:
                logger.debug("[PPT] Unsupported image format=%s — skipping  file='%s'", fmt, fname)
                return ""
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            w, h = image.size
            if w * h < _MIN_IMAGE_PIXELS:
                logger.debug("[PPT] Skipping small image  pixels=%d  file='%s'", w * h, fname)
                return ""
            return self._ocr.image_to_text(image)
        except Exception as exc:
            logger.warning("[PPT] Image OCR failed  error=%s  file='%s'", exc, fname)
            return ""
