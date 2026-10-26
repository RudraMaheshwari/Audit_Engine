from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from src.config.constants import AGENT_RISK_EVALUATION
from src.prompts.risk_evaluation_prompt import risk_evaluation_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RiskEvaluationAgent:
    """
    Reasoning agent that identifies compliance gaps and determines risk level.
    Receives accumulated policy + evidence context from the three retrieval agents.
    No retrieval tools — all context is passed in the message by the supervisor.
    Signals transactional_intent_detected=true to trigger TransactionalDataAgent.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._compiled = self._compile(llm)
        logger.info("[RiskEvaluationAgent] Compiled | name=%s | tools=0 (reasoning only)", AGENT_RISK_EVALUATION)

    def _compile(self, llm: BaseChatModel):
        return create_react_agent(
            model=llm,
            tools=[],
            prompt=risk_evaluation_prompt,
            name=AGENT_RISK_EVALUATION,
        )

    def get_agent(self):
        """Return the compiled LangGraph agent for use by the supervisor."""
        return self._compiled
