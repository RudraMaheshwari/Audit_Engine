from abc import ABC, abstractmethod
from pathlib import Path

from PIL.Image import Image


class BaseOCRProvider(ABC):
    """
    Abstract OCR provider — follows the same abstract pattern as BaseLLMProvider
    and BaseEmbeddingProvider. Concrete implementations: Tesseract, Azure Document Intelligence.
    """

    @abstractmethod
    def image_to_text(self, image: Image) -> str:
        """Extract text from a PIL Image object."""
        ...

    @abstractmethod
    def pdf_to_text(self, file_path: Path) -> list[str]:
        """
        Extract text from every page of an image-based PDF.
        Returns a list of strings, one entry per page.
        """
        ...
