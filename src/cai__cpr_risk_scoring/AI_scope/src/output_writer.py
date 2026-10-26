"""Write the AI Scope output: two sheets.

  Sheet A (AI Scope - Detail): one row per finding — our per-scope summary rows PLUS the Direction-2
    rows appended for source-raised risk types we did not cover (Category '-'). Multiple rows per
    scope area are expected.
  Sheet B (AI Scope - Summary): Sheet A aggregated by scope area — one row per scope. Sample size is
    carried through unchanged, Overall Risk Category is the real category present (ignoring '-'), and
    the summaries/comments are MERGED by concatenation (never re-summarised).
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from src.utils.dateformat import normalize_dates
from src.utils.logger import get_logger
from src.utils.xml_clean import sanitize_xlsx, xml_safe

from .sheet_b_formatter import ScopeSummaryParts, build_summary_cell

try:
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    _RICH_TEXT_OK = True
except ImportError:
    _RICH_TEXT_OK = False

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

def _xml_safe(value):
    """Strip illegal XML chars, truncate to Excel's 32,767-char cell limit, and normalise every date to
    "Month D, YYYY". Single chokepoint for both plain cells and the bold/rich-text path (which calls
    _xml_safe first), so all AI Scope cells are canonical and never trip Excel's repair prompt."""
    if isinstance(value, str):
        return xml_safe(normalize_dates(value))
    return value


def _apply_bold_markup(text):
    text = _xml_safe(text)
    if not text or not _RICH_TEXT_OK:
        return text if not text else re.sub(r"\*\*(.+?)\*\*", r"\1", str(text))
    # Skip empty runs — an empty plain segment or empty bold ("****") writes an empty <r><t></t></r>,
    # which makes Excel "repair String properties" and strip the rich text (bullets → paragraph).
    parts: list = []
    last = 0
    s = str(text)
    for m in _BOLD_RE.finditer(s):
        if m.start() > last:
            seg = s[last:m.start()]
            if seg:
                parts.append(seg)
        bold_text = m.group(1)
        if bold_text and bold_text.strip():
            parts.append(TextBlock(InlineFont(b=True), bold_text))
        elif bold_text:
            parts.append(bold_text)
        last = m.end()
    if last < len(s):
        tail = s[last:]
        if tail:
            parts.append(tail)
    if not parts or all(not isinstance(p, TextBlock) for p in parts):
        return re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    return CellRichText(*parts)

logger = get_logger(__name__)

_HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_THIN = Side(style="thin", color="B0B0B0")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_CATEGORY_RED = "FFC7CE"
_CATEGORY_YELLOW = "FFEB9C"

_SHEET_A_NAME = "AI Scope - Detail"
_SHEET_B_NAME = "AI Scope - Summary"

# (header, width). Column order on each sheet.
_DETAIL_COLUMNS = [
    ("Scope Area", 42), ("Source", 24), ("Scope Decision", 16),
    ("Overall Risk Category", 20), ("Proposed Sample Size", 18), ("Summary of Risks", 90),
    ("Management/Past Audit Source Evidences", 70),
]
_SUMMARY_COLUMNS = [
    ("Scope Area", 42), ("Scope Decision", 16), ("Overall Risk Category", 20),
    ("Proposed Sample Size", 18), ("Summary of Risks", 100), ("Management/Past Audit Source Evidences", 70),
]


def _category_fill(category: str) -> str | None:
    c = (category or "").strip().lower()
    if "high" in c:
        return _CATEGORY_RED
    if "medium" in c or "low" in c:
        return _CATEGORY_YELLOW
    return None


def _band_rank(category: str) -> int:
    """Sort rank for the risk band: High(0) → Medium(1) → Low(2) → Rest(3: No Risk / N-A / '-' / blank)."""
    c = (category or "").strip().lower()
    if "high" in c:
        return 0
    if "medium" in c:
        return 1
    if "low" in c:
        return 2
    return 3


def _order_rows_by_band(rows: list) -> list:
    """Reorder rows so scope areas appear High → Medium → Low → Rest, with each scope's rows kept
    TOGETHER (and in their original relative order). A scope's band is its displayed Overall Risk
    Category — the first real (non-'-') category among its rows — matching Sheet B. Stable within a band
    by first appearance, so Sheet A and the Sheet B aggregation (which follows row order) stay in sync."""
    scope_cat: dict[str, str] = {}
    scope_first: dict[str, int] = {}
    for i, row in enumerate(rows):
        scope = row.scope_area
        if scope not in scope_first:
            scope_first[scope] = i
        cat = (row.overall_risk_category or "").strip()
        if cat and cat != "-" and scope not in scope_cat:
            scope_cat[scope] = cat
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda t: (
        _band_rank(scope_cat.get(t[1].scope_area, "")),  # scope's band
        scope_first[t[1].scope_area],                    # keep scopes grouped, by first appearance
        t[0],                                            # preserve each scope's internal row order
    ))
    return [row for _, row in indexed]


def _write_header(ws, columns) -> None:
    for col_idx, (label, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=_xml_safe(label))
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = width
    ws.freeze_panes = "A2"


def _write_values(ws, row_idx: int, values: list, category_col: int, category: str) -> None:
    fill_color = _category_fill(category)
    fill = PatternFill("solid", fgColor=fill_color) if fill_color else None
    for col_idx, value in enumerate(values, start=1):
        cell = ws.cell(row=row_idx, column=col_idx, value=_xml_safe(value))
        cell.border = _BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if fill is not None and col_idx == category_col:
            cell.fill = fill


def _detail_values(row) -> list:
    sample = "" if row.proposed_sample_size is None else row.proposed_sample_size
    return [
        row.scope_area, row.source, row.scope_decision,
        row.overall_risk_category, sample, _apply_bold_markup(row.summary_of_risks),
        getattr(row, "source_evidence", "") or "",
    ]


def _merge_evidence(items: list) -> str:
    """Join per-row Source Evidence for Sheet B — each block prefixed with its source, separated."""
    blocks = [f"[{source}]\n{ev}" for source, ev in items if ev]
    return ("\n" + "─" * 40 + "\n").join(blocks)


def _aggregate_by_scope(rows: list, llm=None) -> list[tuple]:
    """Collapse Sheet-A rows into one per scope area for Sheet B (merge rules in module docstring).

    The Summary of Risks cell is built by the Sheet B formatter: AI-structured system risk points
    followed by an 'Additional Inputs' section listing what each Direction-2 source raised.
    """
    groups: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        scope = row.scope_area
        group = groups.get(scope)
        if group is None:
            group = {
                "scope_decision": row.scope_decision, "category": "", "sample": None,
                "parts": ScopeSummaryParts(), "evidence": [],
            }
            groups[scope] = group
            order.append(scope)
        category = (row.overall_risk_category or "").strip()
        if category and category != "-" and not group["category"]:
            group["category"] = category
        if row.proposed_sample_size is not None and group["sample"] is None:
            group["sample"] = row.proposed_sample_size
        if row.source == "System":
            group["scope_decision"] = row.scope_decision
        group["parts"].add(row.source, (row.summary_of_risks or "").strip())
        ev = (getattr(row, "source_evidence", "") or "").strip()
        if ev:
            group["evidence"].append((row.source, ev))

    out: list[tuple] = []
    for scope in order:
        g = groups[scope]
        out.append((
            scope,
            g["scope_decision"],
            g["category"] or "-",
            "" if g["sample"] is None else g["sample"],
            build_summary_cell(llm, scope, g["parts"]),
            _merge_evidence(g["evidence"]),
        ))
    return out


def write_ai_scope(rows, output_path: str | Path, llm=None) -> tuple[Path, list[tuple]]:
    """Write Sheet A (detail) and Sheet B (aggregated); return (path, sheet_b_rows).

    `llm` (optional) is used to AI-structure the Sheet B 'Summary of Risks' points; when omitted the
    raw system summary is used as-is and only the Additional Inputs section is composed deterministically.
    The returned Sheet B rows (one tuple per scope, matching `_SUMMARY_COLUMNS`) are reused by the Delta
    export so the pushed table mirrors Sheet B without re-aggregating or re-invoking the LLM.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Arrange risks High → Medium → Low → Rest (scopes kept together). Sheet A writes in this order and
    # Sheet B's aggregation follows row order, so both sheets are consistently banded.
    rows = _order_rows_by_band(rows)

    wb = openpyxl.Workbook()

    ws_a = wb.active
    ws_a.title = _SHEET_A_NAME
    _write_header(ws_a, _DETAIL_COLUMNS)
    for row_idx, row in enumerate(rows, start=2):
        _write_values(ws_a, row_idx, _detail_values(row), category_col=4, category=row.overall_risk_category)

    summary_rows = _aggregate_by_scope(rows, llm)
    ws_b = wb.create_sheet(_SHEET_B_NAME)
    _write_header(ws_b, _SUMMARY_COLUMNS)
    for row_idx, values in enumerate(summary_rows, start=2):
        values_b = list(values)
        values_b[4] = _apply_bold_markup(values_b[4])
        _write_values(ws_b, row_idx, values_b, category_col=3, category=values[2])

    wb.save(path)
    sanitize_xlsx(path)  # permanent guarantee: never leave content Excel needs to repair
    logger.info("[AIScope] Wrote %d detail row(s) -> Sheet A + %d aggregated Sheet B row(s) | %s",
                len(rows), len(summary_rows), path)
    return path, summary_rows
