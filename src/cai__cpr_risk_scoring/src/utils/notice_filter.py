"""Suppress the Databricks VectorSearch PAT notice.

The databricks-vectorsearch client prints a "[NOTICE] Using a Personal Authentication Token (PAT)…"
line directly to stdout (not via the logging system, and repeated on each index operation), which
floods the console. This installs a thin stream wrapper that drops only that specific line and passes
everything else through unchanged.
"""

from __future__ import annotations

import sys

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


def suppress_pat_notice() -> None:
    """Install the PAT-notice filter on stdout and stderr (idempotent)."""
    for attr in ("stdout", "stderr"):
        stream = getattr(sys, attr, None)
        if stream is not None and not isinstance(stream, _NoticeFilteredStream):
            setattr(sys, attr, _NoticeFilteredStream(stream))
