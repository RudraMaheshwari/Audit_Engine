from pathlib import Path

from src.ingestion.extractors.base import BaseExtractor
from src.ingestion.extractors.excel_extractor import ExcelExtractor
from src.ingestion.extractors.html_extractor import HtmlExtractor
from src.ingestion.extractors.msg_extractor import MsgExtractor
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.ingestion.extractors.pdf_extractor import PdfExtractor
from src.ingestion.extractors.ppt_extractor import PptExtractor
from src.ingestion.extractors.word_extractor import WordExtractor


class ExtractorFactory:
    """
    Returns the correct extractor for a given file extension.
    Open/Closed — add new extractors without modifying this class.
    A single OCR provider is injected and shared across all format extractors
    that need it (PDF, PPT, Word, Excel, HTML, MSG).
    """

    def __init__(self, ocr_provider: BaseOCRProvider) -> None:
        self._extractors: list[BaseExtractor] = [
            PdfExtractor(ocr_provider),     # pdfplumber text layer + per-page OCR fallback
            ExcelExtractor(ocr_provider),   # openpyxl cells + embedded image OCR
            WordExtractor(ocr_provider),    # python-docx paragraphs/tables + inline image OCR
            PptExtractor(ocr_provider),     # python-pptx text frames + picture shape OCR
            HtmlExtractor(ocr_provider),    # html2text + base64 data-URI image OCR
            MsgExtractor(ocr_provider),     # extract-msg headers/body + image attachment OCR
        ]

    def get(self, file_path: Path) -> BaseExtractor:
        for extractor in self._extractors:
            if extractor.can_handle(file_path):
                return extractor
        raise ValueError(
            f"No extractor registered for file type: {file_path.suffix!r}. "
            f"Supported: {self.supported_extensions}"
        )

    @property
    def supported_extensions(self) -> set[str]:
        result: set[str] = set()
        for e in self._extractors:
            result |= e.supported_extensions
        return result
