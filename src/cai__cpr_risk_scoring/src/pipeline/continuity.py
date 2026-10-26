"""Scope-level run-over-run continuity: flag scopes whose risk set changed vs the previous run.

Compares the current run's aggregated risks against the most recent prior run's per-scope finding
summary (from observability). SCOPE-LEVEL ONLY: scope_area is a controlled value, so the comparison is
reliable. It deliberately does NOT try to match individual risks across runs — risk_type is free-text
LLM output, so per-risk matching would be unreliable and misleading.

Surfaced as a sidecar markdown file next to the output workbook (no change to the output sheets or any
downstream reader) plus a log line. No-op when there is no prior run or nothing changed.
"""

from __future__ import annotations

from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _current_counts(current_risks) -> dict[str, dict[str, int]]:
    """{scope_area: {risk_category: count}} for the current run's aggregated risks."""
    counts: dict[str, dict[str, int]] = {}
    for risk in current_risks:
        scope = getattr(risk, "scope_area", "") or ""
        if not scope:
            continue
        category = getattr(risk, "risk_category", "") or ""
        scope_counts = counts.setdefault(scope, {})
        scope_counts[category] = scope_counts.get(category, 0) + 1
    return counts


def compute_continuity(
    prior: dict[str, dict[str, int]], current_risks
) -> list[str]:
    """Return one human-readable note per scope whose risk count or category mix changed vs the prior
    run. Empty list when there is no prior run (prior == {}) or nothing changed."""
    if not prior:
        return []
    current = _current_counts(current_risks)
    notes: list[str] = []
    for scope in sorted(set(prior) | set(current)):
        pri = prior.get(scope, {})
        cur = current.get(scope, {})
        pri_total = sum(pri.values())
        cur_total = sum(cur.values())
        if pri_total == 0 and cur_total > 0:
            notes.append(
                f"{scope}: not flagged in the prior run → {cur_total} risk(s) now (new this run — verify)."
            )
        elif cur_total == 0 and pri_total > 0:
            notes.append(
                f"{scope}: {pri_total} risk(s) in the prior run → none this run "
                "(dropped — verify whether intentional)."
            )
        elif cur_total != pri_total:
            notes.append(
                f"{scope}: risk count changed {pri_total} → {cur_total} vs the prior run "
                "(verify whether a risk was added, dropped, or merged)."
            )
        elif pri != cur:
            notes.append(
                f"{scope}: same risk count ({cur_total}) but the risk-category mix changed vs the "
                "prior run (verify)."
            )
    return notes


def write_continuity_report(notes: list[str], output_path) -> Path | None:
    """Write the continuity notes to a sidecar markdown file next to the output workbook. No-op (returns
    None) when there are no notes. Best-effort: logs and returns None on any write failure."""
    if not notes:
        return None
    try:
        out = Path(output_path)
        sidecar = out.with_name(f"{out.stem}__prior_run_comparison.md")
        body = "\n".join(f"- {note}" for note in notes)
        sidecar.write_text(
            "# Prior-Run Comparison\n\n"
            "Scope areas whose risk set changed vs the most recent prior run "
            "(scope-level; review each before finalizing).\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
        logger.info("[Continuity] Wrote prior-run comparison | scopes_changed=%d | %s",
                    len(notes), sidecar)
        return sidecar
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Continuity] Could not write prior-run comparison sidecar | %s", exc)
        return None
