"""LangChain callback that records every LLM invocation to the ObservabilityRecorder.

Attribution model
-----------------
Each call is tagged with an ObsContext (run / question / stage / scope / agent). Two mechanisms
supply it so both concurrency styles in the pipeline are covered:

  * ContextVar (`observe(...)`) — for the concurrent per-question workers. asyncio copies the context
    into each task, and `asyncio.to_thread` copies it into worker threads, so a call made anywhere
    under `observe(question_uid=...)` is attributed to that question.
  * Stage default (`set_stage_default(...)`) — for the sequential stages that use their own thread
    pool (Phase-3 transactional, aggregation, past-audit). A ThreadPoolExecutor does not inherit the
    ContextVar, so those stages set a process-wide default that applies to their worker-thread calls.

Resolution order at record time: ContextVar first, then the stage default. The handler never raises
into the model call — any failure is logged and swallowed.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from src.config.constants import (
    OBS_CALL_STATUS_ERROR,
    OBS_CALL_STATUS_OK,
    OBS_DEFAULT_ATTEMPT,
    OBS_STAGE_PER_QUESTION,
)
from src.observability.recorder import ObservabilityRecorder
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ObsContext:
    """Attribution for an LLM call."""

    run_id: str | None = None
    question_uid: str | None = None
    stage: str | None = None
    scope_area: str | None = None
    agent_name: str | None = None
    prompt_name: str | None = None


_ctx: ContextVar[ObsContext | None] = ContextVar("obs_ctx", default=None)


@contextmanager
def observe(**overrides: Any):
    """Layer attribution onto the current ContextVar for the duration of the block."""
    current = _ctx.get() or ObsContext()
    token = _ctx.set(replace(current, **overrides))
    try:
        yield
    finally:
        _ctx.reset(token)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_usage(response: Any) -> dict[str, int | None]:
    """Pull input/output/total (+cached/reasoning) tokens from an LLMResult, tolerant of shape."""
    input_t = output_t = total_t = cached_t = reasoning_t = None

    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if usage:
        input_t = usage.get("prompt_tokens")
        output_t = usage.get("completion_tokens")
        total_t = usage.get("total_tokens")
        cached_t = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        reasoning_t = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")

    # Always merge usage_metadata (the standardised LangChain field). It is the reliable source for
    # cache_read (cached input tokens); llm_output.token_usage frequently omits prompt_tokens_details,
    # which otherwise left cached_tokens=0 and billed all input at the full rate (flat cost).
    try:
        message = response.generations[0][0].message
        meta = getattr(message, "usage_metadata", None) or {}
        if input_t is None:
            input_t = meta.get("input_tokens")
        if output_t is None:
            output_t = meta.get("output_tokens")
        if total_t is None:
            total_t = meta.get("total_tokens")
        if cached_t is None:
            cached_t = (meta.get("input_token_details") or {}).get("cache_read")
        if reasoning_t is None:
            reasoning_t = (meta.get("output_token_details") or {}).get("reasoning")
    except Exception:  # noqa: BLE001
        pass

    if total_t is None and input_t is not None and output_t is not None:
        total_t = input_t + output_t

    return {
        "input_tokens": input_t,
        "output_tokens": output_t,
        "total_tokens": total_t,
        "cached_tokens": cached_t,
        "reasoning_tokens": reasoning_t,
    }


def _response_text(response: Any) -> str | None:
    try:
        gen = response.generations[0][0]
        return getattr(gen, "text", None) or getattr(getattr(gen, "message", None), "content", None)
    except Exception:  # noqa: BLE001
        return None


class ObservabilityTracer(BaseCallbackHandler):
    """Callback handler that writes one obs_llm_call row per LLM invocation."""

    def __init__(self, recorder: ObservabilityRecorder, default_model: str) -> None:
        from src.observability.pricing import compute_cost_usd
        from src.observability.prompt_registry import get_prompt_versions

        self._recorder = recorder
        self._default_model = default_model
        self._compute_cost = compute_cost_usd
        self._starts: dict[str, dict[str, Any]] = {}
        self._starts_lock = threading.Lock()
        self._stage_default: ObsContext | None = None
        # Computed once — prompt versions/hashes don't change during a run.
        self._prompt_versions = get_prompt_versions()

    def _prompt_fields(self, prompt_name: str | None) -> tuple[str | None, str | None, str | None]:
        """Resolve (prompt_name, version, hash) from the registry for a call's prompt_name."""
        if not prompt_name:
            return None, None, None
        info = self._prompt_versions.get(prompt_name)
        if not info:
            return prompt_name, None, None
        return prompt_name, info.get("version"), info.get("hash")

    def set_stage_default(self, context: ObsContext | None) -> None:
        """Set the fallback attribution for thread-pool stages that do not inherit the ContextVar."""
        self._stage_default = context

    def _resolve_context(self) -> ObsContext:
        return _ctx.get() or self._stage_default or ObsContext()

    def on_llm_start(self, serialized, prompts, *, run_id, invocation_params=None, **kwargs) -> None:
        try:
            params = invocation_params or {}
            model = params.get("model") or params.get("model_name") or self._default_model
            with self._starts_lock:
                self._starts[str(run_id)] = {
                    "started_at": _now(),
                    "model": model,
                    "temperature": params.get("temperature"),
                    "context": self._resolve_context(),
                    "request": "\n\n".join(prompts) if prompts else None,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Obs] on_llm_start capture failed | error=%s", exc)

    def on_chat_model_start(self, serialized, messages, *, run_id, invocation_params=None, **kwargs) -> None:
        prompts = []
        try:
            for batch in messages:
                prompts.append("\n".join(getattr(m, "content", "") or "" for m in batch))
        except Exception:  # noqa: BLE001
            prompts = []
        self.on_llm_start(serialized, prompts, run_id=run_id, invocation_params=invocation_params, **kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        start = self._pop_start(run_id)
        try:
            usage = _extract_usage(response)
            ctx = (start or {}).get("context") or self._resolve_context()
            model = (start or {}).get("model") or self._default_model
            prompt_name, prompt_version, prompt_hash = self._prompt_fields(ctx.prompt_name)
            self._recorder.record_llm_call({
                "run_id": ctx.run_id,
                "question_uid": ctx.question_uid,
                "stage": ctx.stage,
                "agent_name": ctx.agent_name,
                "scope_area": ctx.scope_area,
                "prompt_name": prompt_name,
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "model": model,
                "temperature": (start or {}).get("temperature"),
                "attempt": OBS_DEFAULT_ATTEMPT,
                "status": OBS_CALL_STATUS_OK,
                "started_at": (start or {}).get("started_at"),
                "ended_at": _now(),
                "cost_usd": self._compute_cost(
                    model,
                    usage.get("input_tokens") or 0,
                    usage.get("output_tokens") or 0,
                    usage.get("cached_tokens") or 0,
                ),
                "request_payload": (start or {}).get("request"),
                "response_payload": _response_text(response),
                **usage,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Obs] on_llm_end record failed | error=%s", exc)

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        start = self._pop_start(run_id)
        try:
            ctx = (start or {}).get("context") or self._resolve_context()
            prompt_name, prompt_version, prompt_hash = self._prompt_fields(ctx.prompt_name)
            self._recorder.record_llm_call({
                "run_id": ctx.run_id,
                "question_uid": ctx.question_uid,
                "stage": ctx.stage,
                "agent_name": ctx.agent_name,
                "scope_area": ctx.scope_area,
                "prompt_name": prompt_name,
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "model": (start or {}).get("model") or self._default_model,
                "temperature": (start or {}).get("temperature"),
                "attempt": OBS_DEFAULT_ATTEMPT,
                "status": OBS_CALL_STATUS_ERROR,
                "error": str(error),
                "started_at": (start or {}).get("started_at"),
                "ended_at": _now(),
                "request_payload": (start or {}).get("request"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Obs] on_llm_error record failed | error=%s", exc)

    def _pop_start(self, run_id) -> dict[str, Any] | None:
        with self._starts_lock:
            return self._starts.pop(str(run_id), None)


def per_question_default(run_id: str | None) -> ObsContext:
    """The stage default used around the concurrent question loop."""
    return ObsContext(run_id=run_id, stage=OBS_STAGE_PER_QUESTION)
