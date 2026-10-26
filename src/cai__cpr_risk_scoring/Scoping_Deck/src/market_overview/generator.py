"""Generates the SAM Market Overview slide content (Scoping Deck, Slide 2, Field 6).

SAM only; CPI is intentionally out of scope. Grounds the draft in retrieved audit context and styles
it after the prior-auditor references.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from ..ingestion.questionnaire_index import QuestionnaireIndexManager
from ..prompts.market_overview_prompt import (
    MARKET_OVERVIEW_SYSTEM_PROMPT,
    MARKET_OVERVIEW_USER_TEMPLATE,
)
from .context_retriever import MarketContextRetriever
from .reference_library import ReferenceLibrary

logger = get_logger(__name__)


class MarketOverviewGenerator:
    """Produces the Market Overview slide text for the current auditor."""

    def __init__(self, config: ScopingDeckConfig, index: QuestionnaireIndexManager) -> None:
        self._config = config
        self._references = ReferenceLibrary(config.references_dir)
        self._retriever = MarketContextRetriever(config, index)
        self._llm = AzureOpenAILLMProvider(config.settings).get_client()

    def generate(self, context_block: str | None = None, country: str | None = None) -> str:
        """Generate and return the Market Overview slide content.

        Pass a pre-built context_block to reuse retrieval across generators; otherwise it is fetched.
        Pass country to scope the generation to a specific country (e.g. "Australia", "New Zealand").
        """
        auditor_id = self._config.settings.auditor_id
        ctx = context_block or self._retriever.build_context_block()
        country_line = (
            f"\nCOUNTRY FOR THIS SLIDE: {country} — generate market overview for {country} ONLY."
            if country else ""
        )
        user_message = MARKET_OVERVIEW_USER_TEMPLATE.format(
            auditor_id=auditor_id,
            reference_block=self._references.render_block(),
            context_block=ctx,
        ) + country_line

        log_label = f"{auditor_id}/{country}" if country else auditor_id
        logger.info("[MarketOverview] Invoking LLM for %s", log_label)
        response = self._llm.invoke([
            SystemMessage(content=MARKET_OVERVIEW_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        content = (response.content or "").strip()
        logger.info("[MarketOverview] Draft complete | chars=%d | country=%s", len(content), country)
        return content
