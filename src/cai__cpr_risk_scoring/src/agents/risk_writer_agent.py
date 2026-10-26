from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from src.config.constants import AGENT_RISK_WRITER
from src.prompts.risk_writer_prompt import risk_writer_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RiskWriterAgent:
    """
    Phase B-3b: Risk Writer. Receives the classifier's nine-line decision block and
    produces the final structured audit output — risk title, type, category, rationale,
    description, scope area, evidence explanation, and source details.
    No retrieval tools — all context is injected by the supervisor.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._compiled = self._compile(llm)
        logger.info(
            "[RiskWriterAgent] Compiled | name=%s | tools=0 (writing only)",
            AGENT_RISK_WRITER,
        )

    def _compile(self, llm: BaseChatModel):
        return create_react_agent(
            model=llm,
            tools=[],
            prompt=risk_writer_prompt,
            name=AGENT_RISK_WRITER,
        )

    def get_agent(self):
        return self._compiled
