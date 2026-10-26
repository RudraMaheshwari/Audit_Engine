"""Canonical output date formatting: every date shown in any output renders as "Month D, YYYY"
(e.g. "April 18, 2024").

Two entry points:
  • format_date(value)  — format a date/datetime object (or ISO string) to the canonical string.
  • normalize_dates(text) — rewrite UNAMBIGUOUS date substrings inside free text (LLM rationale /
    description, raw samples, etc.) to the canonical form.

Safety: normalize_dates only matches forms that cannot be confused with other tokens — ISO
(YYYY-MM-DD, optionally with a time), "D Month YYYY", "Month D, YYYY", and "DD-Mon-YYYY". It does NOT
touch ambiguous numeric slash dates (e.g. 04/18/2024 vs 18/04/2024 — day/month order unknown), and it
validates month/day ranges, so policy codes (TV-POL-02224), versions (v1.0), page refs, ratios, and
amounts are left untouched.
"""

from __future__ import annotations

import datetime as _dt
import re

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
# name/abbrev (lower) -> month number
_MONTH_LOOKUP: dict[str, int] = {}
for _i, _name in enumerate(_MONTH_NAMES, start=1):
    _MONTH_LOOKUP[_name.lower()] = _i
    _MONTH_LOOKUP[_name.lower()[:3]] = _i

_FULL_OR_ABBR = (
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
)


def format_date(value) -> str:
    """Return a date/datetime/ISO-string as "Month D, YYYY"; pass non-dates back as str unchanged."""
    if value is None:
        return ""
    if isinstance(value, (_dt.date, _dt.datetime)):
        return _canonical(value.year, value.month, value.day)
    # try an ISO-ish string
    text = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        out = _safe_canonical(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if out:
            return out
    return text


def normalize_dates(text: str) -> str:
    """Rewrite unambiguous date substrings in free text to "Month D, YYYY". Leaves everything else
    (including ambiguous slash dates) unchanged."""
    if not text or not isinstance(text, str):
        return text or ""

    # ISO: 2024-04-18  (optionally with a trailing time, which is dropped)
    def _iso(m: re.Match) -> str:
        out = _safe_canonical(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return out or m.group(0)

    text = re.sub(
        r"\b(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b", _iso, text
    )

    # D Month YYYY  /  Dth Month YYYY   (e.g. 18 April 2024, 1st May 2024)
    def _d_month_y(m: re.Match) -> str:
        mon = _MONTH_LOOKUP.get(m.group(2).lower())
        out = _safe_canonical(int(m.group(3)), mon, int(m.group(1))) if mon else None
        return out or m.group(0)

    text = re.sub(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_FULL_OR_ABBR}\s+(\d{{4}})\b",
        _d_month_y, text, flags=re.IGNORECASE,
    )

    # Month D, YYYY  /  Month Dth YYYY   (normalise spacing / ordinals / missing comma)
    def _month_d_y(m: re.Match) -> str:
        mon = _MONTH_LOOKUP.get(m.group(1).lower())
        out = _safe_canonical(int(m.group(3)), mon, int(m.group(2))) if mon else None
        return out or m.group(0)

    text = re.sub(
        rf"\b{_FULL_OR_ABBR}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
        _month_d_y, text, flags=re.IGNORECASE,
    )

    # DD-Mon-YYYY  (e.g. 18-Apr-2024)
    def _dmony(m: re.Match) -> str:
        mon = _MONTH_LOOKUP.get(m.group(2).lower())
        out = _safe_canonical(int(m.group(3)), mon, int(m.group(1))) if mon else None
        return out or m.group(0)

    text = re.sub(
        rf"\b(\d{{1,2}})-{_FULL_OR_ABBR}-(\d{{4}})\b", _dmony, text, flags=re.IGNORECASE,
    )

    return text


def _canonical(year: int, month: int, day: int) -> str:
    # platform-independent (no %-d / %#d); day as a plain int drops any leading zero
    return f"{_MONTH_NAMES[month - 1]} {day}, {year}"


def _safe_canonical(year: int, month: int, day: int) -> str | None:
    """Canonical string only when (y, m, d) is a real calendar date; else None (leave original)."""
    try:
        _dt.date(year, month, day)
    except ValueError:
        return None
    return _canonical(year, month, day)
