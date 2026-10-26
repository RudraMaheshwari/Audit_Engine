from abc import ABC, abstractmethod

from src.models.schemas import AuditState


class BaseAgent(ABC):
    """
    Abstract base for all audit agents.
    Each agent has one job: receive state, process, return updated state (SRP).
    All agents are substitutable through this interface (LSP).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent identifier used in logging and LangGraph node names."""

    @abstractmethod
    async def run(self, state: AuditState) -> AuditState:
        """Process the current audit state and return the updated state."""
