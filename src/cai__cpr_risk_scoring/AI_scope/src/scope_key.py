"""Scope-area name normalisation shared by the Output C loader and the guidance loader.

Output C may store a scope area as a display name ("Tenders") or a scope_id ("TENDERS"); the guidance
workbook keys sheets by display name. Normalising both to a punctuation/space-free lowercase key lets
them match regardless of spelling, and the scope_id resolver bridges the two vocabularies.
"""

from __future__ import annotations

import re


def normalize(value: str) -> str:
    """Lowercase and strip everything except letters and digits."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())
