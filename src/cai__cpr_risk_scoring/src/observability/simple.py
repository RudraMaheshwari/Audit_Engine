"""One-call observability for the auxiliary engines (AI Scope, Scoping Deck).

The main risk pipeline wires the recorder/tracer by hand across many stages. These engines make a
handful of LLM calls and just need token/cost tracing attributed to one run. `trace_run()` does the
whole setup — recorder + schema + tracer attached to the LLM + a started run whose id is the default
attribution for every call — and returns a handle to `finish()`. It is a complete no-op when
observability is disabled (obs_enabled off / driver missing), so callers need no conditionals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.config.constants import (
    OBS_STATUS_COMPLETED,
    OBS_STATUS_FAILED,
    OBS_STAGE_SETUP,
)
from src.config.settings import Settings
from src.infrastructure.llm.azure_openai_provider import set_active_llm_callback
from src.observability.prompt_registry import get_prompt_versions, prompt_bundle_id
from src.observability.recorder import ObservabilityRecorder
from src.observability.tracing import ObsContext, ObservabilityTracer
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RunTrace:
    recorder: ObservabilityRecorder
    run_id: str | None
    started_at: datetime

    def finish(self, *, ok: bool = True) -> None:
        set_active_llm_callback(None)  # stop auto-attaching to any later clients
        self.recorder.finish_run(
            run_id=self.run_id,
            status=OBS_STATUS_COMPLETED if ok else OBS_STATUS_FAILED,
            started_at=self.started_at,
        )


def trace_run(settings: Settings, *, engine: str, auditor_id: str) -> RunTrace:
    """Open an observability run and register a tracer that auto-attaches to EVERY LLM client the
    engine creates afterwards (via AzureOpenAILLMProvider.get_client) — so no per-call wiring is
    needed. Call BEFORE the engine builds its LLM clients. No-op when obs is disabled; always returns
    a handle whose finish() closes the run and unregisters the tracer.

    `engine` labels the run/stage (e.g. "ai_scope", "scoping_deck").
    """
    started_at = datetime.now(timezone.utc)
    recorder = ObservabilityRecorder(settings)
    recorder.ensure_schema()
    run_id = None
    if recorder.enabled:
        try:
            run_id = recorder.start_run(
                auditor_id=auditor_id,
                model=settings.azure_openai_deployment,
                prompt_bundle_id=prompt_bundle_id(),
                prompt_versions={
                    name: f"{info['version']}#{info['hash']}"
                    for name, info in get_prompt_versions().items()
                },
                run_config={"engine": engine},
            )
            tracer = ObservabilityTracer(recorder, default_model=settings.azure_openai_deployment)
            tracer.set_stage_default(ObsContext(run_id=run_id, stage=OBS_STAGE_SETUP, agent_name=engine))
            set_active_llm_callback(tracer)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Obs] %s tracing setup failed — call tracing off | %s", engine, exc)
    return RunTrace(recorder=recorder, run_id=run_id, started_at=started_at)
