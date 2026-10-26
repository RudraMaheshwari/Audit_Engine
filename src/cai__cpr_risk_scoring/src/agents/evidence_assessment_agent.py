from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.prompts.evidence_assessment_prompt import evidence_assessment_prompt
from src.utils.logger import get_logger
from src.vector_store.retrieval_tools import SupportingDocsRetrievalTool

logger = get_logger(__name__)


def build_evidence_assessment_agent(
    llm: BaseChatModel,
    supporting_tool: SupportingDocsRetrievalTool,
):
    """
    Factory — returns a compiled ReAct agent for evidence retrieval.
    Supports two retrieval modes: semantic (broad) and targeted (by document name).
    """

    @tool
    def retrieve_evidence_semantic(query: str, scope_id: str) -> str:
        """Broad semantic search across all supporting evidence documents for the scope."""
        logger.info(
            "[EvidenceAssessment] Semantic retrieval | scope=%s | query_snippet='%s'",
            scope_id, query[:80],
        )
        chunks = supporting_tool.retrieve(query=query, scope_id=scope_id)
        logger.info("[EvidenceAssessment] Semantic retrieved %d chunks", len(chunks))
        return "\n\n---\n\n".join(
            f"[{c.document_name} | score={c.similarity_score:.3f}]\n{c.content}"
            for c in chunks
        ) or "No semantic evidence retrieved."

    @tool
    def retrieve_evidence_targeted(query: str, scope_id: str, document_name: str) -> str:
        """
        Targeted retrieval filtered to a specific document the auditor cited by name.
        Use when the auditor response explicitly mentions a document file (e.g. 'TPI_Report.pdf').
        """
        logger.info(
            "[EvidenceAssessment] Targeted retrieval | scope=%s | document=%s",
            scope_id, document_name,
        )
        chunks = supporting_tool.retrieve_targeted(
            query=query, scope_id=scope_id, document_name=document_name
        )
        logger.info(
            "[EvidenceAssessment] Targeted retrieved %d chunks from '%s'",
            len(chunks), document_name,
        )
        return "\n\n---\n\n".join(
            f"[{c.document_name} | TARGETED | score={c.similarity_score:.3f}]\n{c.content}"
            for c in chunks
        ) or f"No targeted evidence found in document: {document_name}"

    agent = create_react_agent(
        model=llm,
        tools=[retrieve_evidence_semantic, retrieve_evidence_targeted],
        prompt=evidence_assessment_prompt,
        name="evidence_assessment_agent",
    )

    logger.info("[EvidenceAssessment] Agent compiled with %d tools", 2)
    return agent
