from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from src.config.constants import AGENT_RISK_CLASSIFIER
from src.prompts.risk_classifier_prompt import risk_classifier_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RiskClassifierAgent:
    """
    Phase B-3a: Risk Classifier.

    Receives the full upstream context (HCBI policy, TPI policy, supporting evidence,
    risk evaluation output, and optional transactional data) and outputs the
    classification decision block:

        Risk Type / Risk Category / Likelihood / Impact / Evidence Verdict / Pattern Match /
        Scenario Match / Key Policy Requirement / Key Evidence Gap / GL Mismatch /
        Scope Determination

    The Risk Category is reconciled to the deterministic Risk Matrix band (Likelihood x Impact)
    downstream in the supervisor before the writer renders the final output.

    The classification decision is passed to RiskWriterAgent (Phase B-3b), which
    translates it into the final eight-field audit output. This agent performs only
    the decision logic — no formatting, no final output fields.

    No retrieval tools — all context is injected by the supervisor.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        self._compiled = self._compile(llm)
        logger.info(
            "[RiskClassifierAgent] Compiled | name=%s | role=classifier | tools=0",
            AGENT_RISK_CLASSIFIER,
        )

    def _compile(self, llm: BaseChatModel):
        return create_react_agent(
            model=llm,
            tools=[],
            prompt=risk_classifier_prompt,
            name=AGENT_RISK_CLASSIFIER,
        )

    def get_agent(self):
        """Return the compiled LangGraph agent for use by the supervisor."""
        return self._compiled
