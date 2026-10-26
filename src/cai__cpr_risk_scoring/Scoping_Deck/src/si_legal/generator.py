"""Generates the Legal-Counsel, SI-Summary and Additional-Input slide content.

The main app keeps SI, Legal and Additional Input in SEPARATE indexes: kind='legal' queries the
`legal` index, kind='si' queries the `si_history` index, kind='additional' queries the
`additional_input` index. Each kind uses its own seed queries + prompt scoping. Returns bullets, or ''
when the section has no content (the slide is then dropped).
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.infrastructure.embeddings.azure_openai_provider import AzureOpenAIEmbeddingProvider
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider
from src.utils.logger import get_logger
from src.vector_store.index_manager import VectorIndexManager

from ..config import ScopingDeckConfig
from ..prompts.si_legal_prompts import (
    ADDITIONAL_INPUT_SYSTEM_PROMPT,
    ADDITIONAL_INPUT_USER_TEMPLATE,
    LEGAL_RISKS_SYSTEM_PROMPT,
    LEGAL_RISKS_USER_TEMPLATE,
    SI_SUMMARY_SYSTEM_PROMPT,
    SI_SUMMARY_USER_TEMPLATE,
)

logger = get_logger(__name__)

# SI, Legal and Additional Input are stored in separate main-app indexes; each kind reads only its own.
_LEGAL_INDEX = "legal"
_SI_INDEX = "si_history"
_ADDITIONAL_INDEX = "additional_input"

_KIND: dict[str, dict] = {
    "legal": {
        "system": LEGAL_RISKS_SYSTEM_PROMPT,
        "user": LEGAL_RISKS_USER_TEMPLATE,
        "index": _LEGAL_INDEX,
        "log": "Legal",
        "seeds": [
            "risks and observations noted by legal counsel",
            "legal input on compliance risks, documentation, and approvals",
            "legal counsel concerns on HCP payments, grants, FFS contracts, business justification",
            "legal review observations on HCC activities and record-keeping",
        ],
    },
    "si": {
        "system": SI_SUMMARY_SYSTEM_PROMPT,
        "user": SI_SUMMARY_USER_TEMPLATE,
        "index": _SI_INDEX,
        "log": "SI",
        "seeds": [
            "sensitive issue investigation findings and themes",
            "SI history: verified issues, misconduct, non-compliance",
            "issues with distributors, TPIs, payments to HCPs and government officials",
            "conflict of interest, expense fraud, asset misappropriation, product diversion",
        ],
    },
    "additional": {
        "system": ADDITIONAL_INPUT_SYSTEM_PROMPT,
        "user": ADDITIONAL_INPUT_USER_TEMPLATE,
        "index": _ADDITIONAL_INDEX,
        "log": "AdditionalInput",
        "seeds": [
            "risks and observations noted in the additional inputs for this audit",
            "additional compliance concerns, documentation gaps, and approval issues",
            "additional input on HCP payments, grants, FFS contracts, and third parties",
            "other noted risks and themes relevant to the compliance review",
        ],
    },
}


class SiLegalSlideGenerator:
    """Generates the Legal-Counsel (kind='legal'), SI-Summary (kind='si') or Additional-Input
    (kind='additional') slide bullets."""

    def __init__(self, config: ScopingDeckConfig, kind: str) -> None:
        if kind not in _KIND:
            raise ValueError(f"Unknown kind: {kind!r}")
        self._config = config
        self._spec = _KIND[kind]
        self._llm = AzureOpenAILLMProvider(config.settings).get_client()

    def generate(self, audit_entity: str) -> str:
        """Return the slide bullets, or '' when the section has no content (slide then skipped)."""
        context_block = self._retrieve()
        if not context_block.strip():
            logger.info("[%sSlide] No knowledge-base content retrieved — slide will be skipped",
                        self._spec["log"])
            return ""
        user = self._spec["user"].format(audit_entity=audit_entity, context_block=context_block)
        logger.info("[%sSlide] Invoking LLM to draft bullets", self._spec["log"])
        response = self._llm.invoke([
            SystemMessage(content=self._spec["system"]),
            HumanMessage(content=user),
        ])
        content = (response.content or "").strip()
        logger.info("[%sSlide] Draft complete | chars=%d", self._spec["log"], len(content))
        return content

    def _retrieve(self) -> str:
        auditor_id = self._config.settings.auditor_id
        try:
            provider = AzureOpenAIEmbeddingProvider(self._config.settings)
            index_manager = VectorIndexManager(self._config.settings, provider)
        except Exception as exc:
            logger.warning("[%sSlide] Index manager init failed | %s", self._spec["log"], exc)
            return ""

        filters = {"auditor_folder": auditor_id}
        index_key = self._spec["index"]

        merged: dict[str, str] = {}
        for query in self._spec["seeds"]:
            try:
                hits = index_manager.similarity_search(
                    index_key=index_key, query=query, scope_id=None,
                    top_k=self._config.supporting_docs_top_k, filters=filters,
                )
            except Exception as exc:
                logger.warning("[%sSlide] Retrieval failed for seed '%s' | %s",
                               self._spec["log"], query[:40], exc)
                continue
            for chunk in hits:
                if chunk.chunk_id not in merged and (chunk.content or "").strip():
                    merged[chunk.chunk_id] = f"[{chunk.document_name}] {chunk.content.strip()}"

        snippets = list(merged.values())[: self._config.max_context_chunks]
        logger.info("[%sSlide] Retrieved %d unique KB chunk(s)", self._spec["log"], len(snippets))
        return "\n\n".join(snippets)
