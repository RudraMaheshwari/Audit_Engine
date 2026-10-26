from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.config.constants import AGENT_HCBI_POLICY, HCBI_SCOPE_ID
from src.prompts.hcbi_retrieval_prompt import hcbi_retrieval_prompt
from src.tools.hcbi_retrieval_tool import HcbiRetrievalTool
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HcbiPolicyAgent:
    """
    Retrieval agent for the HCBI Global Policy index (hcbi_index).
    HCBI is the universal compliance baseline — queried first for every audit question.
    """

    def __init__(self, llm: BaseChatModel, hcbi_tool: HcbiRetrievalTool) -> None:
        self._compiled = self._compile(llm, hcbi_tool)
        logger.info("[HcbiPolicyAgent] Compiled | name=%s | index=hcbi_index | tools=1", AGENT_HCBI_POLICY)

    def _compile(self, llm: BaseChatModel, hcbi_tool: HcbiRetrievalTool):
        @tool
        def retrieve_hcbi_policy(query: str) -> str:
            """
            Search the HCBI Global Compliance Policy index (hcbi_index).
            Returns the top-5 most relevant policy sections for the audit question.
            """
            logger.info("[HcbiPolicyAgent] Retrieving | query='%s'", query[:80])
            chunks = hcbi_tool.retrieve(query=query, scope_id=HCBI_SCOPE_ID)
            logger.info("[HcbiPolicyAgent] Retrieved %d chunks from hcbi_index", len(chunks))

            if not chunks:
                return "No relevant HCBI policy sections found."

            return "\n\n---\n\n".join(
                f"[{c.document_name} | Page No.: {c.metadata.get('page_number') or 'Not Available'}]\n{c.content}"
                for c in chunks
            )

        return create_react_agent(
            model=llm,
            tools=[retrieve_hcbi_policy],
            prompt=hcbi_retrieval_prompt,
            name=AGENT_HCBI_POLICY,
        )

    def get_agent(self):
        """Return the compiled LangGraph agent for use by the supervisor."""
        return self._compiled
