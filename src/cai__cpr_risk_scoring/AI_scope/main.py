"""AI Scope generator — entry point.

Reads Output C (from an Excel workbook's 3rd sheet or a Delta table), groups it by scope area, and
prints one AI Scope row per scope area with the 4 fields:
  Scope Area | Overall Risk Category | Proposed Sample Size | Summary of Risks

Runs independently of the main pipeline but reuses the same .env / Settings.

Env vars:
  AI_SCOPE_SOURCE        "excel" or "db_table"
  AI_SCOPE_PATH          excel workbook path  OR  fully-qualified Delta table
  AI_SCOPE_OUTPUT_E_PATH  (optional, db_table only) Output E (Past Audit) Delta table
  AI_SCOPE_OUTPUT_F_PATH  (optional, db_table only) Output F (Scope Txn) Delta table
  AI_SCOPE_GUIDANCE_PATH (optional) CPR guidance workbook for sample sizes

Usage (from the project root):
  python -m AI_Scope.main
"""

from __future__ import annotations

import os
import sys

# Make the project root importable so the main app's `src` package resolves regardless of CWD.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv()

from datetime import datetime

from .src.ai_scope_builder import build_ai_scope
from .src.config import load_config
from .src.output_writer import write_ai_scope
try:
    from src.utils.logger import get_logger
except ModuleNotFoundError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        _logger = logging.getLogger(name)
        if not _logger.handlers:
            _handler = logging.StreamHandler()
            _handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
            _logger.addHandler(_handler)
            _logger.setLevel(logging.INFO)
        return _logger

logger = get_logger(__name__)

# Default output location: AI_Scope/output/ (override with AI_SCOPE_OUTPUT_PATH).
_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def _resolve_output_path(auditor_id: str) -> str:
    override = (os.getenv("AI_SCOPE_OUTPUT_PATH", "") or "").strip()
    if override:
        return override
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(_OUTPUT_DIR, f"AI_Scope_{auditor_id}_{stamp}.xlsx")


def _print_rows(rows) -> None:
    print("\n" + "=" * 80)
    print("AI SCOPE")
    print("=" * 80)
    if not rows:
        print("(no scope areas produced)")
        return
    for row in rows:
        avg = "" if row.average_score is None else f"  [avg L×I = {row.average_score} over {row.scored_rows} risk row(s)]"
        print("\n" + "-" * 80)
        print(f"Scope Area            : {row.scope_area}")
        print(f"Overall Risk Category : {row.overall_risk_category or '(blank — no L/I)'}{avg}")
        print(f"Proposed Sample Size  : {row.proposed_sample_size if row.proposed_sample_size is not None else '(blank — not in guidance)'}")
        print("Summary of Risks      :")
        print(row.summary_of_risks or "  (none)")
    print("\n" + "=" * 80)


def _fallback_suppress_pat_notice() -> None:
    """Inline fallback when src.utils.notice_filter is unavailable."""
    _NEEDLE = "Using a Personal Authentication Token"
    class _NoticeFilteredStream:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped
        def write(self, s):
            if _NEEDLE in s:
                kept = "".join(line for line in s.splitlines(keepends=True) if _NEEDLE not in line)
                if kept:
                    self._wrapped.write(kept)
                return len(s)
            return self._wrapped.write(s)
        def flush(self):
            self._wrapped.flush()
        def __getattr__(self, name):
            return getattr(self._wrapped, name)
    for attr in ("stdout", "stderr"):
        stream = getattr(sys, attr, None)
        if stream is not None and not isinstance(stream, _NoticeFilteredStream):
            setattr(sys, attr, _NoticeFilteredStream(stream))


def main() -> None:
    try:
        from src.utils.notice_filter import suppress_pat_notice
    except ModuleNotFoundError:
        suppress_pat_notice = _fallback_suppress_pat_notice
    suppress_pat_notice()

    config = load_config()
    logger.info(
        "=== AI Scope | source=%s | path=%s ===", config.source, config.path
    )
    rows = build_ai_scope(config)
    _print_rows(rows)

    if rows:
        out_path = _resolve_output_path(config.settings.auditor_id)
        try:
            from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider  # noqa: E501
            llm = AzureOpenAILLMProvider(config.settings).get_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AIScope] Could not build LLM for Sheet B structuring — using raw summaries | %s", exc)
            llm = None
        saved, summary_rows = write_ai_scope(rows, out_path, llm=llm)
        print(f"\nSaved AI Scope output to: {saved}")

        # Mirror the AI Scope SUMMARY (Sheet B — one row per scope) to a Delta table
        # (AI_Scope_<engagement>). Best-effort.
        try:
            from src.repositories.delta_export import DeltaOutputExporter  # noqa: E501

            headers = [
                "Scope Area", "Scope Decision", "Overall Risk Category",
                "Proposed Sample Size", "Summary of Risks", "Management/Past Audit Source Evidences",
            ]
            data = [list(values) for values in summary_rows]
            DeltaOutputExporter(config.settings).export_rows("AI_Scope", headers, data, also_copy=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AIScope] Delta export skipped | %s", exc)


if __name__ == "__main__":
    main()
