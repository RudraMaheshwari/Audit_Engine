from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.config.constants import AGENT_SUPPORTING_DOCS
from src.prompts.supporting_docs_prompt import supporting_docs_prompt
from src.tools.evidence_retrieval_tool import EvidenceRetrievalTool
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SupportingDocsAgent:
    """
    Retrieval agent for the supporting evidence index (supporting_docs_index).
    Supports two modes: broad semantic search and targeted search by cited document name.
    """

    def __init__(self, llm: BaseChatModel, evidence_tool: EvidenceRetrievalTool) -> None:
        self._compiled = self._compile(llm, evidence_tool)
        logger.info(
            "[SupportingDocsAgent] Compiled | name=%s | index=supporting_docs_index | tools=2 (semantic + targeted)",
            AGENT_SUPPORTING_DOCS,
        )

    def _compile(self, llm: BaseChatModel, evidence_tool: EvidenceRetrievalTool):
        @tool
        def retrieve_supporting_docs_semantic(query: str) -> str:
            """
            Broad semantic search across all supporting evidence documents in
            supporting_docs_index. Returns top-5 most relevant chunks.
            Always call this first before targeted retrieval.
            """
            logger.info(
                "[SupportingDocsAgent] Semantic retrieval | query='%s'", query[:80],
            )
            chunks = evidence_tool.retrieve(query=query)
            logger.info(
                "[SupportingDocsAgent] Semantic retrieved %d chunks", len(chunks),
            )

            if not chunks:
                return "No supporting evidence found via semantic search."

            return "\n\n---\n\n".join(
                f"[{c.document_name} | SEMANTIC]\n{c.content}"
                for c in chunks
            )

        @tool
        def retrieve_supporting_docs_targeted(query: str, document_name: str) -> str:
            """
            Targeted retrieval filtered to a specific document file cited by name in the auditor
            response (e.g. 'TPI_Screening_Report.pdf'). Use only when a specific file is mentioned.
            """
            logger.info(
                "[SupportingDocsAgent] Targeted retrieval | document='%s'", document_name,
            )
            chunks = evidence_tool.retrieve_targeted(
                query=query, document_name=document_name
            )
            logger.info(
                "[SupportingDocsAgent] Targeted retrieved %d chunks from '%s'",
                len(chunks), document_name,
            )

            if not chunks:
                return f"No targeted evidence found in document: {document_name}"

            return "\n\n---\n\n".join(
                f"[{c.document_name} | TARGETED]\n{c.content}"
                for c in chunks
            )

        return create_react_agent(
            model=llm,
            tools=[retrieve_supporting_docs_semantic, retrieve_supporting_docs_targeted],
            prompt=supporting_docs_prompt,
            name=AGENT_SUPPORTING_DOCS,
        )

    def get_agent(self):
        """Return the compiled LangGraph agent for use by the supervisor."""
        return self._compiled
