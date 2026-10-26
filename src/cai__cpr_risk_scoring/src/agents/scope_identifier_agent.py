from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from src.config.constants import AGENT_SCOPE_IDENTIFIER
from src.prompts.scope_identifier_prompt import scope_identifier_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ScopeIdentifierAgent:
    """
    Phase 0: Scope Identifier.
    Analyses the full question + response + follow-up context and identifies
    which of the 48 FCPA audit scope areas the content belongs to.
    Output feeds into Phase A so retrieval is focused on the right scope(s).
    No retrieval tools — all context injected by supervisor.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._compiled = self._compile(llm)
        logger.info(
            "[ScopeIdentifierAgent] Compiled | name=%s | role=phase0 | tools=0",
            AGENT_SCOPE_IDENTIFIER,
        )

    def _compile(self, llm: BaseChatModel):
        return create_react_agent(
            model=llm,
            tools=[],
            prompt=scope_identifier_prompt,
            name=AGENT_SCOPE_IDENTIFIER,
        )

    def get_agent(self):
        return self._compiled
