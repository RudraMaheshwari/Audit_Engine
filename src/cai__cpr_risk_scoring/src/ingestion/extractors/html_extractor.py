import base64
import io
import re
from pathlib import Path

import html2text
from bs4 import BeautifulSoup
from PIL import Image

from src.config.constants import OCR_SUPPORTED_IMAGE_FORMATS
from src.ingestion.extractors.base import BaseExtractor
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.models.schemas import ExtractedDocument
from src.utils.logger import get_logger, log_extraction_summary

logger = get_logger(__name__)

_NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript"]

_DATA_URI_RE = re.compile(r"data:image/(?P<fmt>\w+);base64,(?P<data>[A-Za-z0-9+/=]+)")

_MIN_B64_LENGTH = 500


class HtmlExtractor(BaseExtractor):
    """
    Extracts clean readable text from HTML files and OCRs base64-embedded images.
    File is read in binary mode (rb) then decoded, preserving encoding fidelity.
    Inline data-URI images (base64 encoded) are decoded and passed through OCR.
    """

    def __init__(self, ocr_provider: BaseOCRProvider) -> None:
        self._ocr = ocr_provider
        self._converter = html2text.HTML2Text()
        self._converter.ignore_links = True
        self._converter.ignore_images = True
        self._converter.body_width = 0

    @property
    def supported_extensions(self) -> set[str]:
        return {"html", "htm"}

    def extract(self, file_path: Path, scope_id: str) -> ExtractedDocument:
        fname = file_path.name

        raw_bytes = file_path.read_bytes()
        raw_html = raw_bytes.decode("utf-8", errors="replace")
        raw_size = len(raw_bytes)

        logger.info(
            "[HTML] ▶ START  file='%s'  raw_bytes=%d  scope=%s", fname, raw_size, scope_id,
        )

        soup = BeautifulSoup(raw_html, "html.parser")

        removed_tags = {tag: len(soup.find_all(tag)) for tag in _NOISE_TAGS}
        for tag in _NOISE_TAGS:
            for el in soup(tag):
                el.decompose()
        logger.debug(
            "[HTML] Noise tags removed  tags=%s  file='%s'",
            {k: v for k, v in removed_tags.items() if v > 0}, fname,
        )

        text_content = self._converter.handle(str(soup)).strip()
        logger.debug("[HTML] Text extracted  chars=%d  file='%s'", len(text_content), fname)

        ocr_parts: list[str] = []
        images_ocr = 0
        for img_tag in soup.find_all("img"):
            src = img_tag.get("src", "")
            m = _DATA_URI_RE.match(src)
            if not m or len(m.group("data")) < _MIN_B64_LENGTH:
                continue
            logger.debug(
                "[HTML] Running OCR on base64 image  b64_len=%d  file='%s'",
                len(m.group("data")), fname,
            )
            ocr_text = self._ocr_data_uri(m.group("data"), fname)
            if ocr_text:
                ocr_parts.append(f"[Embedded Image OCR]\n{ocr_text}")
                images_ocr += 1
                logger.debug(
                    "[HTML] Image OCR done  chars=%d  file='%s'", len(ocr_text), fname,
                )

        content = text_content
        if ocr_parts:
            content += "\n\n" + "\n\n".join(ocr_parts)

        logger.info(
            "[HTML] ✔ DONE  file='%s'  raw_bytes=%d  noise_removed=%d  "
            "images_ocr=%d  content_chars=%d",
            fname, raw_size, sum(removed_tags.values()), images_ocr, len(content),
        )
        log_extraction_summary(
            logger, fname, "HTML", len(content),
            extra={
                "raw_bytes":          raw_size,
                "noise_tags_removed": sum(removed_tags.values()),
                "images_ocr":         images_ocr,
            },
        )

        return ExtractedDocument(
            file_path=str(file_path),
            file_name=fname,
            file_type="html",
            content=content,
            scope_id=scope_id,
            metadata={
                "extractor":          "html2text+beautifulsoup4+ocr",
                "raw_size_bytes":     raw_size,
                "noise_tags_removed": removed_tags,
                "images_ocr":         images_ocr,
            },
        )

    def _ocr_data_uri(self, b64_data: str, fname: str = "") -> str:
        """Decode a base64 image data URI and run OCR on it."""
        try:
            img_bytes = base64.b64decode(b64_data)
            probe = Image.open(io.BytesIO(img_bytes))
            fmt = probe.format
            probe.close()
            if fmt not in OCR_SUPPORTED_IMAGE_FORMATS:
                logger.debug("[HTML] Unsupported image format=%s — skipping  file='%s'", fmt, fname)
                return ""
            pil_image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            return self._ocr.image_to_text(pil_image)
        except Exception as exc:
            logger.warning("[HTML] Image OCR failed  error=%s  file='%s'", exc, fname)
            return ""
