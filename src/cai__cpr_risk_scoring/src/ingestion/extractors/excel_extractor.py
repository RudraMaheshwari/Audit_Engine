import io
import zipfile
from pathlib import Path

import openpyxl
from PIL import Image

from src.config.constants import OCR_SUPPORTED_IMAGE_FORMATS
from src.ingestion.extractors.base import BaseExtractor
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.models.schemas import ExtractedDocument
from src.utils.logger import get_logger, log_extraction_summary

try:
    import xlrd
    _HAS_XLRD = True
except ImportError:
    _HAS_XLRD = False

logger = get_logger(__name__)


class ExcelExtractor(BaseExtractor):
    """
    Extracts cell data from all sheets and OCRs embedded images/charts.
    File is opened from binary bytes (rb) so the same bytes can be passed to openpyxl
    without relying on file-system path availability.
    """

    def __init__(self, ocr_provider: BaseOCRProvider) -> None:
        self._ocr = ocr_provider

    @property
    def supported_extensions(self) -> set[str]:
        return {"xlsx", "xls", "xlsm"}

    def extract(self, file_path: Path, scope_id: str) -> ExtractedDocument:
        fname = file_path.name
        raw_bytes = file_path.read_bytes()
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        except zipfile.BadZipFile:
            if _HAS_XLRD:
                return self._extract_xls_legacy(file_path, raw_bytes, scope_id)
            logger.warning("[Excel] Not a valid xlsx (legacy binary .xls or corrupted) — skipping  file='%s'", fname)
            return ExtractedDocument(file_path=str(file_path), file_name=fname, file_type="xlsx", content="", scope_id=scope_id, metadata={"extractor": "openpyxl+ocr", "error": "not_a_zip_file"})
        sheet_count = len(wb.sheetnames)
        logger.info(
            "[Excel] ▶ START  file='%s'  sheets=%d  scope=%s",
            fname, sheet_count, scope_id,
        )

        sheet_texts: list[str] = []
        total_rows = 0
        total_images_ocr = 0

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts: list[str] = []

            rows: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip()]
                if cells:
                    rows.append(" | ".join(cells))
            total_rows += len(rows)
            if rows:
                parts.append("\n".join(rows))
                logger.debug(
                    "[Excel] sheet='%s'  rows=%d  file='%s'", sheet_name, len(rows), fname,
                )

            for img_obj in getattr(ws, "_images", []):
                ocr_text = self._ocr_excel_image(img_obj, sheet_name, fname)
                if ocr_text:
                    parts.append(f"[Embedded Image OCR]\n{ocr_text}")
                    total_images_ocr += 1
                    logger.debug(
                        "[Excel] Image OCR done  sheet='%s'  chars=%d  file='%s'",
                        sheet_name, len(ocr_text), fname,
                    )

            if parts:
                sheet_texts.append(f"[Sheet: {sheet_name}]\n" + "\n".join(parts))

        wb.close()
        content = "\n\n".join(sheet_texts)

        logger.info(
            "[Excel] ✔ DONE  file='%s'  sheets=%d  total_rows=%d  images_ocr=%d  content_chars=%d",
            fname, sheet_count, total_rows, total_images_ocr, len(content),
        )
        log_extraction_summary(
            logger, fname, "Excel", len(content),
            extra={
                "sheets":      sheet_count,
                "total_rows":  total_rows,
                "images_ocr":  total_images_ocr,
            },
        )

        return ExtractedDocument(
            file_path=str(file_path),
            file_name=fname,
            file_type="xlsx",
            content=content,
            scope_id=scope_id,
            metadata={
                "extractor":           "openpyxl+ocr",
                "sheets":              wb.sheetnames,
                "total_rows_extracted": total_rows,
                "images_ocr":          total_images_ocr,
            },
        )

    def _extract_xls_legacy(self, file_path: Path, raw_bytes: bytes, scope_id: str) -> ExtractedDocument:
        fname = file_path.name
        try:
            wb = xlrd.open_workbook(file_contents=raw_bytes)
            sheet_texts: list[str] = []
            total_rows = 0
            for sheet_name in wb.sheet_names():
                ws = wb.sheet_by_name(sheet_name)
                rows: list[str] = []
                for row_idx in range(ws.nrows):
                    cells = [str(ws.cell_value(row_idx, col)).strip() for col in range(ws.ncols)]
                    cells = [c for c in cells if c and c != "None"]
                    if cells:
                        rows.append(" | ".join(cells))
                total_rows += len(rows)
                if rows:
                    sheet_texts.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows))
            content = "\n\n".join(sheet_texts)
            logger.info("[Excel] Legacy XLS extracted | file='%s' | sheets=%d | rows=%d | chars=%d", fname, wb.nsheets, total_rows, len(content))
            return ExtractedDocument(
                file_path=str(file_path), file_name=fname, file_type="xls",
                content=content, scope_id=scope_id,
                metadata={"extractor": "xlrd-legacy", "sheets": wb.sheet_names(), "total_rows_extracted": total_rows},
            )
        except Exception as exc:
            logger.warning("[Excel] Legacy XLS fallback also failed  error=%s  file='%s'", exc, fname)
            return ExtractedDocument(file_path=str(file_path), file_name=fname, file_type="xls", content="", scope_id=scope_id, metadata={"extractor": "xlrd-legacy", "error": str(exc)})

    def _ocr_excel_image(self, img_obj, sheet_name: str = "", fname: str = "") -> str:
        """Extract binary from an openpyxl image object and run OCR."""
        try:
            if hasattr(img_obj, "_data") and callable(img_obj._data):
                raw = img_obj._data()
            elif hasattr(img_obj, "ref") and hasattr(img_obj.ref, "getvalue"):
                raw = img_obj.ref.getvalue()
            else:
                return ""
            probe = Image.open(io.BytesIO(raw))
            fmt = probe.format
            probe.close()
            if fmt not in OCR_SUPPORTED_IMAGE_FORMATS:
                logger.debug("[Excel] Unsupported image format=%s — skipping  sheet='%s'  file='%s'", fmt, sheet_name, fname)
                return ""
            pil_image = Image.open(io.BytesIO(raw)).convert("RGB")
            return self._ocr.image_to_text(pil_image)
        except Exception as exc:
            logger.warning(
                "[Excel] Image OCR failed  sheet='%s'  error=%s  file='%s'",
                sheet_name, exc, fname,
            )
            return ""
