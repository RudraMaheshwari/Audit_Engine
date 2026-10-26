import io
from pathlib import Path

import extract_msg
from PIL import Image

from src.config.constants import OCR_SUPPORTED_IMAGE_FORMATS
from src.ingestion.extractors.base import BaseExtractor
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.models.schemas import ExtractedDocument
from src.utils.logger import get_logger, log_extraction_summary

logger = get_logger(__name__)

_IMAGE_MIME_PREFIXES = ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/bmp", "image/tiff")

_MIN_IMAGE_BYTES = 5_000


class MsgExtractor(BaseExtractor):
    """
    Extracts email header, body, and inline/attached images from Outlook .msg files.
    File is opened in binary mode (rb) via extract_msg's bytes interface.
    Image attachments and CID-inline images are passed through OCR.
    Non-image attachments (PDF, DOCX, etc.) are logged by name only —
    DocumentLoader handles them separately when they exist as standalone files.
    """

    def __init__(self, ocr_provider: BaseOCRProvider) -> None:
        self._ocr = ocr_provider

    @property
    def supported_extensions(self) -> set[str]:
        return {"msg"}

    def extract(self, file_path: Path, scope_id: str) -> ExtractedDocument:
        fname = file_path.name
        logger.info("[MSG] ▶ START  file='%s'  scope=%s", fname, scope_id)

        raw_bytes = file_path.read_bytes()

        with extract_msg.openMsg(io.BytesIO(raw_bytes)) as msg:
            parts: list[str] = []

            subject  = (msg.subject or "").strip()
            sender   = (msg.sender or "").strip()
            date_str = str(msg.date) if msg.date else ""
            to_field = (msg.to or "").strip()
            body     = (msg.body or "").strip()

            logger.debug(
                "[MSG] Headers parsed  subject='%s'  sender='%s'  date='%s'  file='%s'",
                subject[:60], sender[:60], date_str, fname,
            )

            if subject:  parts.append(f"Subject: {subject}")
            if sender:   parts.append(f"From: {sender}")
            if date_str: parts.append(f"Date: {date_str}")
            if to_field: parts.append(f"To: {to_field}")
            if body:     parts.append(f"\n{body}")

            attachment_names: list[str] = []
            images_ocr = 0
            non_image_attachments: list[str] = []

            for attachment in msg.attachments:
                att_name = (
                    attachment.longFilename
                    or attachment.shortFilename
                    or "unnamed"
                ).strip()
                attachment_names.append(att_name)

                mime = (getattr(attachment, "mimetype", "") or "").lower()

                if any(mime.startswith(p) for p in _IMAGE_MIME_PREFIXES):
                    att_data = attachment.data
                    if att_data and len(att_data) >= _MIN_IMAGE_BYTES:
                        logger.debug(
                            "[MSG] Running OCR on image attachment  name='%s'  bytes=%d  file='%s'",
                            att_name, len(att_data), fname,
                        )
                        ocr_text = self._ocr_attachment(att_data, att_name, fname)
                        if ocr_text:
                            parts.append(f"[Image Attachment: {att_name}]\n{ocr_text}")
                            images_ocr += 1
                            logger.debug(
                                "[MSG] Attachment OCR done  name='%s'  chars=%d  file='%s'",
                                att_name, len(ocr_text), fname,
                            )
                    else:
                        logger.debug(
                            "[MSG] Skipping small image attachment  name='%s'  bytes=%d  file='%s'",
                            att_name, len(att_data) if att_data else 0, fname,
                        )
                else:
                    non_image_attachments.append(att_name)
                    logger.debug(
                        "[MSG] Non-image attachment (skipped for OCR)  name='%s'  mime='%s'  file='%s'",
                        att_name, mime, fname,
                    )

            content = "\n".join(parts)

        logger.info(
            "[MSG] ✔ DONE  file='%s'  body_chars=%d  attachments=%d  "
            "images_ocr=%d  non_image=%d  content_chars=%d",
            fname, len(body), len(attachment_names),
            images_ocr, len(non_image_attachments), len(content),
        )
        log_extraction_summary(
            logger, fname, "MSG", len(content),
            extra={
                "subject":               subject[:60] if subject else "N/A",
                "body_chars":            len(body),
                "attachments_total":     len(attachment_names),
                "attachments_image_ocr": images_ocr,
                "attachments_non_image": non_image_attachments[:5],
            },
        )

        return ExtractedDocument(
            file_path=str(file_path),
            file_name=fname,
            file_type="msg",
            content=content,
            scope_id=scope_id,
            metadata={
                "extractor":             "extract-msg+ocr",
                "subject":               subject,
                "sender":                sender,
                "date":                  date_str,
                "attachments":           attachment_names,
                "images_ocr":            images_ocr,
                "non_image_attachments": non_image_attachments,
            },
        )

    def _ocr_attachment(self, data: bytes, name: str, fname: str = "") -> str:
        """Run OCR on raw image attachment bytes."""
        try:
            probe = Image.open(io.BytesIO(data))
            fmt = probe.format
            probe.close()
            if fmt not in OCR_SUPPORTED_IMAGE_FORMATS:
                logger.debug("[MSG] Unsupported image format=%s — skipping  name='%s'  file='%s'", fmt, name, fname)
                return ""
            pil_image = Image.open(io.BytesIO(data)).convert("RGB")
            return self._ocr.image_to_text(pil_image)
        except Exception as exc:
            logger.warning(
                "[MSG] Attachment OCR failed  name='%s'  error=%s  file='%s'",
                name, exc, fname,
            )
            return ""
