"""Observability for the AI Auditor.

Holds the prompt version registry (traceability) and the Lakebase Postgres run-tracing layer:
one run row, one row per question, one row per LLM call, and one row per emitted finding —
capturing input/output/total tokens, cost, latency, confidence and thresholds for each run.
"""

from src.observability.recorder import ObservabilityRecorder
from src.observability.tracing import (
    ObsContext,
    ObservabilityTracer,
    observe,
    per_question_default,
)

__all__ = [
    "ObservabilityRecorder",
    "ObservabilityTracer",
    "ObsContext",
    "observe",
    "per_question_default",
]
