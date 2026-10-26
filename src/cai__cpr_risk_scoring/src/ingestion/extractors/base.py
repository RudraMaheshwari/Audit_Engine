from abc import ABC, abstractmethod
from pathlib import Path

from src.models.schemas import ExtractedDocument


class BaseExtractor(ABC):
    """Abstract base class for all document extractors. One extractor per file type (SRP)."""

    @property
    @abstractmethod
    def supported_extensions(self) -> set[str]:
        """File extensions this extractor handles (lowercase, without dot)."""

    @abstractmethod
    def extract(self, file_path: Path, scope_id: str) -> ExtractedDocument:
        """Extract text content from a file and return a validated ExtractedDocument."""

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower().lstrip(".") in self.supported_extensions
