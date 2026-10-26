from abc import ABC, abstractmethod
from typing import Any

from src.models.schemas import DocumentChunk


class BaseRetrievalTool(ABC):
    """Abstract base for all index retrieval tools (SRP — one concrete class per index)."""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        scope_id: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        ...
