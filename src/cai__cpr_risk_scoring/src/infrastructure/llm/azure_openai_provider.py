from functools import cached_property

from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI

from src.config.settings import Settings
from src.infrastructure.llm.base import BaseLLMProvider

# Optional observability callback auto-attached to every client handed out by get_client(). Left None
# for the main pipeline (which attaches its own tracer); set by src.observability.simple.trace_run for
# the auxiliary engines so their scattered LLM clients are all traced without per-call wiring.
_ACTIVE_LLM_CALLBACK = None


def set_active_llm_callback(handler) -> None:
    """Register (or clear with None) a callback handler auto-attached to new LLM clients."""
    global _ACTIVE_LLM_CALLBACK
    _ACTIVE_LLM_CALLBACK = handler


class AzureOpenAILLMProvider(BaseLLMProvider):
    """
    Azure OpenAI implementation of BaseLLMProvider.
    Uses GPT-5 via the JNJ gateway with temperature=0 for deterministic audit outputs.
    Client is cached after first access — one instance reused across all agents.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @cached_property
    def _client(self) -> AzureChatOpenAI:
        return AzureChatOpenAI(
            azure_endpoint=self._settings.azure_openai_endpoint,
            azure_deployment=self._settings.azure_openai_deployment,
            api_key=self._settings.azure_openai_api_key,
            api_version=self._settings.azure_openai_api_version,
            max_retries=3,
        )

    def get_client(self) -> BaseChatModel:
        client = self._client
        if _ACTIVE_LLM_CALLBACK is not None:
            existing = list(getattr(client, "callbacks", None) or [])
            if _ACTIVE_LLM_CALLBACK not in existing:
                client.callbacks = existing + [_ACTIVE_LLM_CALLBACK]
        return client
