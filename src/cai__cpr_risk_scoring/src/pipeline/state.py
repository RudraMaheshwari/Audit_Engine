from typing import Annotated
from typing_extensions import TypedDict

from src.models.schemas import AuditState


def _overwrite(current, new):
    """LangGraph reducer — always take the latest value."""
    return new


class GraphState(TypedDict):
    """
    LangGraph node state. Uses Annotated reducers so each node can update
    the state without conflicts. The AuditState Pydantic model carries all data;
    GraphState is the thin LangGraph-compatible wrapper.
    """
    audit_state: Annotated[AuditState, _overwrite]
    error: Annotated[str | None, _overwrite]
