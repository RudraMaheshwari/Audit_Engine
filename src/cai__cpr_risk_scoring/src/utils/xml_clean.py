"""Make Office XML (xlsx / pptx) safe so it never triggers the "repair / remove content" prompt.

Three failure modes cause Office to repair a file:
  1. Characters illegal in XML 1.0 (C0/C1 controls, surrogates, non-characters).
  2. A single cell/run longer than Excel's hard limit of 32,767 characters.
  3. Empty rich-text runs (<r><t></t></r>) — openpyxl writes these when a bold marker is empty.

xml_safe() handles (1)+(2) at the point of writing a value. sanitize_xlsx() is the PERMANENT backstop:
after the workbook is saved it cleans the final file bytes — stripping residual illegal chars, removing
empty runs, AND truncating any over-length <t> run to Excel's 32,767-char limit — across EVERY sheet, so
no writer path, present or future (including openpyxl load/re-save round-trips), can produce a file that
Excel needs to repair.
"""

from __future__ import annotations

import os
import re
import zipfile

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Illegal in XML 1.0: C0 controls (except \t \n \r), DEL, C1 controls, surrogates, and the
# non-characters U+FDD0–U+FDEF and U+FFFE/U+FFFF (all can make Excel/PowerPoint "repair" the file).
_ILLEGAL_XML_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f\ud800-\udfff﷐-﷯￾￿]")

# An EMPTY rich-text run: <r>[optional <rPr>…</rPr>]<t …></t></r> or the self-closing <t …/> form.
# Only matches runs with NO content at all (a whitespace run has content and is left untouched). The
# rPr inner is tempered with (?!</?r\b) so it can never backtrack across a run boundary (<r>/</r>) —
# it still allows </rPr> (r→P is not a word boundary), just not </r>.
_EMPTY_RUN_RE = re.compile(
    r"<r>(?:<rPr\b(?:(?!</?r\b).)*?</rPr>)?<t\b[^>]*></t></r>"
    r"|<r>(?:<rPr\b(?:(?!</?r\b).)*?</rPr>)?<t\b[^>]*/></r>",
    re.DOTALL,
)

EXCEL_CELL_LIMIT = 32767

# A single text run <t …>CONTENT</t>. Used by the post-save backstop to truncate any run that exceeds
# Excel's hard cell limit (the one corruption cause xml_safe must also catch at the bytes level).
_LONG_T_RE = re.compile(r"(<t\b[^>]*>)(.*?)(</t>)", re.DOTALL)
# A dangling partial XML entity at the end of a slice (e.g. '&amp' / '&#x1F') — trimmed so truncation
# never cuts through an entity and produces malformed XML.
_DANGLING_ENTITY_RE = re.compile(r"&[^;]{0,8}$")


def _truncate_long_runs(text: str) -> str:
    """Truncate the CONTENT of any <t>…</t> run longer than EXCEL_CELL_LIMIT (entity-safe), so an
    over-length cell can never reach Excel and trigger a repair. Operates on the raw part bytes."""
    def _trunc(match: "re.Match") -> str:
        inner = match.group(2)
        if len(inner) <= EXCEL_CELL_LIMIT:
            return match.group(0)
        cut = _DANGLING_ENTITY_RE.sub("", inner[: EXCEL_CELL_LIMIT - 1])
        return f"{match.group(1)}{cut}…{match.group(3)}"
    return _LONG_T_RE.sub(_trunc, text)


def xml_safe(value, limit: int = EXCEL_CELL_LIMIT):
    """Strip illegal XML chars and truncate to `limit`. Pass non-strings through unchanged."""
    if not isinstance(value, str):
        return value
    cleaned = _ILLEGAL_XML_RE.sub("", value)
    if limit and len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


# A PowerPoint text run <a:t …>CONTENT</a:t>.
_A_T_RE = re.compile(r"(<a:t\b[^>]*>)(.*?)(</a:t>)", re.DOTALL)

# An EMPTY PowerPoint run: <a:r>[optional <a:rPr …> … </a:rPr> or self-closing]<a:t …></a:t></a:r>
# (or a self-closing <a:t …/>). Matches ONLY a run with no text content — a whitespace run has content
# and is left untouched. The rPr inner is tempered so it can never backtrack across a run boundary
# (<a:r>/</a:r>); </a:rPr> is still allowed (r→P is not a word boundary). python-pptx writes these for
# blank table cells / empty lines, and PowerPoint "repairs (removes) string properties" when it sees them.
_EMPTY_A_RUN_RE = re.compile(
    r"<a:r>(?:<a:rPr\b[^>]*/>|<a:rPr\b(?:(?!</?a:r\b).)*?</a:rPr>)?"
    r"(?:<a:t\b[^>]*></a:t>|<a:t\b[^>]*/>)</a:r>",
    re.DOTALL,
)


def _clean_xlsx_part(text: str) -> str:
    """xlsx worksheet / sharedStrings: strip illegal chars, drop empty runs, truncate over-length runs."""
    return _truncate_long_runs(_EMPTY_RUN_RE.sub("", _ILLEGAL_XML_RE.sub("", text)))


def _clean_pptx_part(text: str) -> str:
    """pptx slide/layout/master/notes XML: strip illegal chars, remove EMPTY runs (blank table cells /
    empty lines write <a:r><a:t/></a:r>, which PowerPoint repairs away), then for every <a:t> run replace
    raw line breaks with a space (a literal newline in a run makes PowerPoint 'repair and remove' the
    slide) and truncate over-length run text — both entity-safe."""
    text = _EMPTY_A_RUN_RE.sub("", _ILLEGAL_XML_RE.sub("", text))

    def _fix(match: "re.Match") -> str:
        inner = match.group(2)
        fixed = inner.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        if len(fixed) > EXCEL_CELL_LIMIT:
            fixed = _DANGLING_ENTITY_RE.sub("", fixed[: EXCEL_CELL_LIMIT - 1]) + "…"
        return f"{match.group(1)}{fixed}{match.group(3)}" if fixed != inner else match.group(0)

    return _A_T_RE.sub(_fix, text)


def _sanitize_office_zip(path, part_predicate, clean_fn, label: str) -> None:
    """Rewrite a SAVED Office OOXML file in place: for every part matching `part_predicate`, apply
    `clean_fn` to its XML text. Best-effort and self-contained — any failure leaves the original file
    untouched (logged, never raised), so saving is never broken by the cleanup."""
    path = str(path)
    tmp = path + ".sanitize.tmp"
    try:
        with zipfile.ZipFile(path, "r") as zin:
            items = zin.infolist()
            parts = {i.filename: zin.read(i.filename) for i in items}

        changed = False
        for name, data in parts.items():
            if not part_predicate(name):
                continue
            text = data.decode("utf-8", errors="ignore")
            cleaned = clean_fn(text)
            if cleaned != text:
                parts[name] = cleaned.encode("utf-8")
                changed = True

        if not changed:
            return
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for i in items:
                zout.writestr(i, parts[i.filename])
        os.replace(tmp, path)
        logger.info("[xml_clean] sanitized %s (illegal chars / empty runs / over-length / line breaks) | %s",
                    label, path)
    except Exception as exc:  # noqa: BLE001 — never break the save on cleanup failure
        logger.warning("[xml_clean] post-save sanitize skipped | %s | %s", path, exc)
        try:
            os.remove(tmp)
        except OSError:
            pass


def sanitize_xlsx(path) -> None:
    """Permanent backstop so Excel never prompts to repair: clean every worksheet / sharedStrings part."""
    _sanitize_office_zip(
        path,
        lambda n: n.endswith(".xml") and ("worksheets/sheet" in n or n.endswith("sharedStrings.xml")),
        _clean_xlsx_part,
        "workbook",
    )


def sanitize_pptx(path) -> None:
    """Permanent backstop so PowerPoint never prompts to repair: clean every slide / layout / master /
    notes XML part (illegal chars, raw newlines inside a run, and over-length run text)."""
    _sanitize_office_zip(
        path,
        lambda n: n.endswith(".xml") and (
            n.startswith("ppt/slides/") or n.startswith("ppt/slideLayouts/")
            or n.startswith("ppt/slideMasters/") or n.startswith("ppt/notesSlides/")
        ),
        _clean_pptx_part,
        "presentation",
    )
