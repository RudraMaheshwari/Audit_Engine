"""FastAPI application layer for the AI Auditor.

Exposes the three long-running workflows as HTTP APIs — Risk Analysis, Scoping AI, and Scoping Deck —
each started as a background job that reports granular status codes. The API layer orchestrates the
existing `src/`, `AI_scope/`, and `Scoping_Deck/` engines; it never re-implements them (SRP).
"""

__version__ = "0.1.0"
