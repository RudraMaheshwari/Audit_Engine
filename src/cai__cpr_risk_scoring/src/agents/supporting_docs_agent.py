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
        self._evidence_tool = evidence_tool
        self._compiled = self._compile(llm, evidence_tool)
        logger.info(
            "[SupportingDocsAgent] Compiled | name=%s | index=supporting_docs_index | tools=2 (semantic + targeted)",
            AGENT_SUPPORTING_DOCS,
        )

    # Multi-part questions (e.g. "a. TPI inventory ... b. red flags under mitigation") let the ReAct
    # agent's single query focus on one part and silently drop the other. Red-flag/mitigation evidence
    # is high-value and easily missed, so fetch it DETERMINISTICALLY when the question asks for it —
    # independent of whatever query the agent chose. One retrieval call; no agent loop.
    _RED_FLAG_TERMS = ("red flag", "red-flag", "redflag")
    _RED_FLAG_QUERY = (
        "red flag third party mitigation status monitoring under progress remediation decision "
        "authority RWG conditionally approve FCPA bribery corruption due diligence"
    )

    @staticmethod
    def _is_red_flag_name(name: str) -> bool:
        norm = "".join(ch for ch in (name or "").lower() if ch.isalnum())
        return "redflag" in norm

    async def red_flag_supplement(self, question_text: str, followup_question: str = "") -> str:
        """When the question asks about red flags / mitigations, directly retrieve that evidence so it
        can't be dropped. Pulls EVERY red-flag-named file BY NAME (targeted) — so a critical record (e.g.
        the one carrying 'Mitigation Status = Not Started') is never lost to semantic top-k variance,
        which otherwise flips the finding between Risk and No Risk run to run. Falls back to a broad
        semantic pass when no red-flag-named file exists. '' when not a red-flag question / nothing found."""
        text = f"{question_text}\n{followup_question}".lower()
        if not any(t in text for t in self._RED_FLAG_TERMS):
            return ""

        names = sorted(n for n in self._evidence_tool.doc_names() if self._is_red_flag_name(n))
        chunks = []
        for n in names:
            try:
                chunks.extend(await self._evidence_tool.aretrieve_targeted(query=self._RED_FLAG_QUERY, document_name=n))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SupportingDocsAgent] red-flag targeted retrieve failed | doc=%s | %s", n, exc)

        if not chunks:  # no red-flag-named file (or targeting failed) → broad semantic fallback
            try:
                chunks = await self._evidence_tool.aretrieve(query=self._RED_FLAG_QUERY)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SupportingDocsAgent] red-flag supplement failed | %s", exc)
                return ""

        if not chunks:
            return ""
        logger.info(
            "[SupportingDocsAgent] Red-flag supplement | targeted_files=%d | chunks=%d | docs=%s",
            len(names), len(chunks), "; ".join(sorted({c.document_name for c in chunks})),
        )
        body = "\n\n---\n\n".join(f"[{c.document_name} | TARGETED]\n{c.content}" for c in chunks)
        return "RED-FLAG / MITIGATION EVIDENCE (deterministic supplement):\n" + body

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
