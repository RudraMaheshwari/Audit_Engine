"""Generates the 'Risks noted by Management' slide content (Scoping Deck, Slide 4).

Retrieves, from the SHARED questionnaire index the main app populates at the start of risk analysis
(`runner._ensure_questionnaire_index`), the risk areas the audited entity's management themselves named
in their responses to opinion-of-risk questions, and returns bullets — but only when such risks were
actually noted (empty string otherwise, so the slide is skipped).

The deck CONSUMES the index via the main app's VectorIndexManager.search_questionnaire (its own
schema/population); it does not maintain a separate questionnaire source.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

from ..config import ScopingDeckConfig
from ..prompts.risks_noted_prompt import (
    RISKS_NOTED_SYSTEM_PROMPT,
    RISKS_NOTED_USER_TEMPLATE,
)

logger = get_logger(__name__)


class RisksNotedGenerator:
    """Produces the 'Risks noted by Management' bullets for the current auditor."""

    # Risk-opinion seed queries: surface the questions that ask the respondent's own view of the
    # company's risks (as opposed to the market/business-overview retrieval).
    _SEED_QUERIES = [
        "what do you consider the biggest compliance or FCPA risks in the company",
        "key risk areas or exposures management wants considered in the review",
        "corruption, bribery, or fraud concerns noted by the business respondent",
        "areas of concern, weaknesses, or vulnerabilities the respondent highlighted",
        "what keeps you up at night; where is the company most exposed to compliance risk",
        "which HCC activities do you consider the biggest risk to the company, country, or region",
        "which business units or product lines generate the biggest HCC or FCPA risk and why",
        "the biggest expenses or payment types that pose FCPA risk, e.g. payments to HCPs, hospitals, or government agencies",
        "regional, upcoming, or focus-area compliance activities or projects that may impact risk",
        "vendors or third parties the respondent thinks may be a corruption concern",
    ]

    def __init__(self, config: ScopingDeckConfig) -> None:
        self._config = config
        self._llm = AzureOpenAILLMProvider(config.settings).get_client()

    def generate(self, audit_entity: str) -> str:
        """Return the risk bullets, or '' when management noted no risks (slide then skipped)."""
        auditor_id = self._config.settings.auditor_id
        context_block = self._retrieve(auditor_id)
        if not context_block.strip():
            logger.info("[RisksNoted] No questionnaire context retrieved — slide will be skipped")
            return ""

        user_message = RISKS_NOTED_USER_TEMPLATE.format(
            audit_entity=audit_entity, context_block=context_block,
        )
        logger.info("[RisksNoted] Invoking LLM to extract management-noted risks for auditor=%s", auditor_id)
        response = self._llm.invoke([
            SystemMessage(content=RISKS_NOTED_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        content = (response.content or "").strip()
        logger.info("[RisksNoted] Draft complete | chars=%d", len(content))
        return content

    def _retrieve(self, auditor_id: str) -> str:
        """Query the shared questionnaire index (main-app owned) across the risk-opinion seeds."""
        try:
            provider = AzureOpenAIEmbeddingProvider(self._config.settings)
            index_manager = VectorIndexManager(self._config.settings, provider)
        except Exception as exc:
            logger.warning("[RisksNoted] Index manager init failed | %s", exc)
            return ""

        merged: dict[str, str] = {}
        for query in self._SEED_QUERIES:
            try:
                hits = index_manager.search_questionnaire(
                    query=query, auditor_id=auditor_id, top_k=self._config.retrieval_top_k,
                )
            except Exception as exc:
                logger.warning("[RisksNoted] Retrieval failed for seed '%s' | %s", query[:40], exc)
                continue
            for chunk in hits:
                if chunk.chunk_id not in merged and (chunk.content or "").strip():
                    merged[chunk.chunk_id] = f"[{chunk.document_name}]\n{chunk.content.strip()}"

        snippets = list(merged.values())[: self._config.max_context_chunks]
        logger.info("[RisksNoted] Retrieved %d unique questionnaire chunk(s)", len(snippets))
        return "\n\n".join(snippets)
