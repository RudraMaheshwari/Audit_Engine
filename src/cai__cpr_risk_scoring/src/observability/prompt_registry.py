"""Prompt version registry — single source of truth for prompt versions + content hashes.

WHY
    Each output is a compliance risk assessment; we must be able to answer "which prompt produced
    this row?" for traceability, reproducibility, rollback, and to correlate quality/feedback with
    prompt changes.

WHAT (today — Option A, cheap prep, no infra)
    • A semantic VERSION per prompt (bumped when you intentionally change behavior).
    • An automatic CONTENT HASH of each prompt file (detects drift even if the version isn't bumped).
    • `log_prompt_versions()` stamps the bundle into the run log at start — so every run is traceable
      via the output file's timestamp + the logged bundle id. It does NOT change the Excel output.

OBSERVABILITY HOOK (later — when the observability layer is appended)
    This module is the single place that layer should read from. Promote it without changing call
    sites by having the observability layer call `prompt_version_metadata()` and attach it to:
      • OTEL span attributes (per trace/step),
      • an MLflow Prompt Registry entry, or
      • a Lakebase Postgres `prompt_versions` table joined to the feedback table.
    Keep `prompt_version_metadata()` returning a FLAT dict so it maps cleanly to span attrs / a DB row.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Bump the version when a prompt's BEHAVIOR changes intentionally. The content hash is computed
# automatically from the file, so any edit is detectable regardless of the version number.
_PROMPT_VERSIONS: dict[str, str] = {
    "risk_classifier":        "3.4",
    "risk_writer":            "3.4",
    "risk_evaluation":        "2.3",
    "scope_identifier":       "2.1",
    "policy_knowledge":       "2.0",
    "hcbi_retrieval":         "1.1",
    "supporting_docs":        "1.1",
    "transactional_analysis": "2.1",
    "transactional_data":     "1.2",
    "supervisor":             "2.1",
    "guardrails":             "1.1",
    "risk_aggregation":       "1.2",
    "past_audit_summary":     "1.0",
    "transactional_data_dictionary": "1.6",
}

_PROMPT_FILES: dict[str, str] = {
    "risk_classifier":        "risk_classifier_prompt.py",
    "risk_writer":            "risk_writer_prompt.py",
    "risk_evaluation":        "risk_evaluation_prompt.py",
    "scope_identifier":       "scope_identifier_prompt.py",
    "policy_knowledge":       "policy_knowledge_prompt.py",
    "hcbi_retrieval":         "hcbi_retrieval_prompt.py",
    "supporting_docs":        "supporting_docs_prompt.py",
    "transactional_analysis": "transactional_analysis_prompt.py",
    "transactional_data":     "transactional_data_prompt.py",
    "supervisor":             "supervisor_prompt.py",
    "guardrails":             "guardrails.py",
    "risk_aggregation":       "risk_aggregation_prompt.py",
    "past_audit_summary":     "past_audit_summary_prompt.py",
    "transactional_data_dictionary": "transactional_data_dictionary.py",
}


def _file_hash(filename: str) -> str:
    try:
        return hashlib.sha256((_PROMPT_DIR / filename).read_bytes()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return "unknown"


def get_prompt_versions() -> dict[str, dict[str, str]]:
    """Return {prompt_name: {"version": <semver>, "hash": <12-char content hash>}}."""
    return {
        name: {"version": _PROMPT_VERSIONS.get(name, "0.0"), "hash": _file_hash(fname)}
        for name, fname in _PROMPT_FILES.items()
    }


def prompt_bundle_id() -> str:
    """A single short id for the whole prompt set this run — changes if ANY prompt/version changes.
    Suitable for tagging a run, an OTEL trace, or an output-metadata field."""
    versions = get_prompt_versions()
    combined = "|".join(
        f"{name}:{versions[name]['version']}:{versions[name]['hash']}"
        for name in sorted(versions)
    )
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:12]


def prompt_version_metadata() -> dict[str, str]:
    """FLAT dict for observability sinks (OTEL span attributes / MLflow tags / a Postgres row).
    Keys look like 'prompt.risk_classifier.version' / 'prompt.risk_classifier.hash', plus the bundle id."""
    meta: dict[str, str] = {"prompt.bundle_id": prompt_bundle_id()}
    for name, info in get_prompt_versions().items():
        meta[f"prompt.{name}.version"] = info["version"]
        meta[f"prompt.{name}.hash"] = info["hash"]
    return meta


def log_prompt_versions(logger) -> str:
    """Stamp the prompt bundle + per-prompt versions into the run log. Returns the bundle id."""
    bundle = prompt_bundle_id()
    versions = {k: f"{v['version']}#{v['hash']}" for k, v in get_prompt_versions().items()}
    logger.info("[Prompts] bundle_id=%s | versions=%s", bundle, versions)
    return bundle
