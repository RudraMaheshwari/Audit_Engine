from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.config.constants import AGENT_POLICY_KNOWLEDGE, SCOPE_UNFILTERED_LABEL
from src.prompts.policy_knowledge_prompt import policy_knowledge_prompt
from src.tools.policy_retrieval_tool import PolicyRetrievalTool
from src.tools.scope_policy_retrieval_tool import ScopePolicyRetrievalTool
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PolicyKnowledgeAgent:
    """
    Retrieval agent with two policy tools:

    1. retrieve_scope_area_policy(query, scope_id) — always called.
       Queries fcpa_scope_policy_index filtered by the FCPA scope area identified by
       ScopeIdentifierAgent. Returns Risk Profile + Audit Guidance for that area.

    2. retrieve_global_scope_area_policies(query) — called when the content concerns any scope
       area's policy/controls. Queries the "global_scope_area_policies" index for the full policy
       documents (SOPs, guidelines, work instructions) across scope areas. Provides detailed
       policy text beyond the CPR guide-book summary.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        scope_policy_tool: ScopePolicyRetrievalTool,
        dd_policy_tool: PolicyRetrievalTool,
    ) -> None:
        self._compiled = self._compile(llm, scope_policy_tool, dd_policy_tool)
        logger.info(
            "[PolicyKnowledgeAgent] Compiled | name=%s | tools=2 "
            "(scope_area_policy + global_scope_area_policies)",
            AGENT_POLICY_KNOWLEDGE,
        )

    def _compile(
        self,
        llm: BaseChatModel,
        scope_policy_tool: ScopePolicyRetrievalTool,
        dd_policy_tool: PolicyRetrievalTool,
    ):
        @tool
        def retrieve_scope_area_policy(query: str, scope_id: str) -> str:
            """
            Search fcpa_scope_policy_index to retrieve the Risk Profile and Audit Guidance most
            relevant to the query. Retrieval is scope-agnostic (scope is assigned later), so
            ALWAYS pass scope_id='ANY_SCOPE': this runs UNFILTERED across all 48 scope areas by
            semantic relevance and returns a wider set of the most relevant chunks. Do not pass a
            specific scope id. Always call this first.
            """
            logger.info(
                "[PolicyKnowledgeAgent] scope_area_policy | scope=%s | query='%s'",
                scope_id, query[:80],
            )
            chunks = scope_policy_tool.retrieve(query=query, scope_id=scope_id)
            logger.info(
                "[PolicyKnowledgeAgent] scope_area_policy retrieved %d chunks | scope=%s",
                len(chunks), scope_id,
            )
            if not chunks:
                return f"No CPR policy guidance found for scope area: {scope_id}"
            return "\n\n---\n\n".join(
                f"[{c.document_name} | {c.metadata.get('chunk_type', 'policy')} | scope={c.scope_id}]\n{c.content}"
                for c in chunks
            )

        @tool
        def retrieve_global_scope_area_policies(query: str) -> str:
            """
            Search the "global_scope_area_policies" index for the full policy documents (SOPs,
            guidelines, work instructions) governing the relevant scope area(s). Call this when
            the question/response concerns any scope area's policy or controls (e.g. third-party
            onboarding/due diligence, fee-for-service, congresses, sponsorships, tenders, patient
            support programs, government affairs, distributors). Decide by content, not by a scope
            label. It supplements the CPR guide-book summary with detailed policy text from the
            actual documents. Retrieval is scope-agnostic (searches across all scope areas).
            """
            logger.info(
                "[PolicyKnowledgeAgent] global_scope_area_policies | query='%s'", query[:80],
            )
            chunks = dd_policy_tool.retrieve(query=query, scope_id=SCOPE_UNFILTERED_LABEL)
            logger.info(
                "[PolicyKnowledgeAgent] global_scope_area_policies retrieved %d chunks", len(chunks),
            )
            if not chunks:
                return "No relevant scope-area policy document sections found."
            return "\n\n---\n\n".join(
                f"[{c.document_name} | Page No.: {c.metadata.get('page_number') or 'Not Available'}]\n{c.content}"
                for c in chunks
            )

        return create_react_agent(
            model=llm,
            tools=[retrieve_scope_area_policy, retrieve_global_scope_area_policies],
            prompt=policy_knowledge_prompt,
            name=AGENT_POLICY_KNOWLEDGE,
        )

    def get_agent(self):
        """Return the compiled LangGraph agent for use by the supervisor."""
        return self._compiled
