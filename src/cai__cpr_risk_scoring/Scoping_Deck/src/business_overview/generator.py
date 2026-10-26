"""Generates the six Business Overview sections in one grounded, reference-styled LLM call."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.utils.logger import get_logger

from ..config import ScopingDeckConfig
from ..ingestion.questionnaire_index import QuestionnaireIndexManager
from ..market_overview.context_retriever import MarketContextRetriever
from ..market_overview.reference_library import ReferenceLibrary
from ..prompts.business_overview_prompt import (
    BUSINESS_OVERVIEW_SYSTEM_PROMPT,
    BUSINESS_OVERVIEW_USER_TEMPLATE,
)

logger = get_logger(__name__)

_SECTION_KEYS = (
    "business_overview",
    "changes_and_challenges",
    "go_to_market",
    "interactions",
    "tpi_overview",
    "key_regulations",
)


class BusinessOverviewGenerator:
    """Produces the Business Overview sections as a {section_key: bullet_text} map."""

    def __init__(self, config: ScopingDeckConfig, index: QuestionnaireIndexManager) -> None:
        self._config = config
        self._references = ReferenceLibrary(config.business_references_dir)
        self._retriever = MarketContextRetriever(config, index)
        self._llm = AzureOpenAILLMProvider(config.settings).get_client()

    def generate(
        self, context_block: str | None = None, entity_override: str | None = None
    ) -> dict[str, str]:
        """Return the six sections keyed by section name; each value is bullet text ('- ' lines).

        Pass a pre-built context_block to reuse retrieval across generators; otherwise it is fetched.
        Pass entity_override to use a different entity label (e.g. "IM Australia" / "IM New Zealand").
        """
        entity = entity_override or self._config.entity_name
        ctx = context_block or self._retriever.build_context_block()
        country_hint = ""
        if entity_override:
            country_hint = (
                f"\nCOUNTRY SCOPE: generate Business Overview for {entity_override} ONLY. "
                "Focus on facts specific to that country's operations."
            )
        user_message = BUSINESS_OVERVIEW_USER_TEMPLATE.format(
            entity=entity,
            reference_block=self._references.render_block(),
            context_block=ctx,
        ) + country_hint
        logger.info("[BusinessOverview] Invoking LLM for auditor=%s entity=%s",
                    self._config.settings.auditor_id, entity)
        try:
            response = self._llm.invoke([
                SystemMessage(content=BUSINESS_OVERVIEW_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            sections = self._parse(response.content)
        except Exception as exc:
            logger.error("[BusinessOverview] Generation/parse failed | %s", exc)
            sections = {}
        suffix = ""
        if entity_override:
            # Map "IM Australia" → "_au", "IM New Zealand" → "_nz", etc.
            tag = entity_override.lower().split()[-1][:2]   # "australia"→"au", "zealand"→"ze"→"nz"
            if "zealand" in entity_override.lower():
                tag = "nz"
            elif "australia" in entity_override.lower():
                tag = "au"
            suffix = f"_{tag}"
        return {f"{key}{suffix}": self._to_bullets(sections.get(key, [])) for key in _SECTION_KEYS}

    @staticmethod
    def _parse(content: str) -> dict:
        raw = (content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}

    # Hard cap so a Business Overview cell can never overflow the slide, regardless of what the LLM returns.
    _MAX_BULLETS_PER_SECTION = 3

    @classmethod
    def _to_bullets(cls, value) -> str:
        items = value if isinstance(value, list) else [value]
        lines = [f"- {str(item).strip()}" for item in items if str(item).strip()]
        return "\n".join(lines[: cls._MAX_BULLETS_PER_SECTION])
