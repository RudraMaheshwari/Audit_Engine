from src.config.settings import Settings
from src.ingestion.extractors.ocr.azure_document_intelligence_provider import (
    AzureDocumentIntelligenceOCRProvider,
)
from src.ingestion.extractors.ocr.base import BaseOCRProvider
from src.ingestion.extractors.ocr.tesseract_provider import TesseractOCRProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OCRProviderFactory:
    """
    Selects and instantiates the correct OCR provider.
    Follows the same factory pattern as LLMProvider and EmbeddingProvider.

    Primary:  Azure Document Intelligence (production — JNJ Azure environment)
    Fallback: Tesseract (local dev / CI when Azure DI is not configured)
    """

    @staticmethod
    def create(settings: Settings) -> BaseOCRProvider:
        if (
            settings.azure_document_intelligence_endpoint
            and settings.azure_document_intelligence_key
        ):
            logger.info("[OCRFactory] Using Azure Document Intelligence OCR")
            return AzureDocumentIntelligenceOCRProvider(
                endpoint=settings.azure_document_intelligence_endpoint,
                api_key=settings.azure_document_intelligence_key,
            )

        logger.warning(
            "[OCRFactory] Azure DI credentials not configured — falling back to Tesseract OCR. "
            "Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY for production."
        )
        return TesseractOCRProvider()
