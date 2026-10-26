"""Reshape an Output A Excel IN PLACE into the merged 'Questions & Responses' column, PRESERVING all
existing cell formatting (fills/colors, fonts, borders, column widths, header styling).

It collapses the question / response (and follow-up) columns — whether raw (separate Question /
Response / Follow-up …) or already merged (Question & Response / Follow-up Question & Response) — into
ONE 'Questions & Responses' cell, formatted exactly like the pipeline's Output A:
    [<#>] <Questionnaire File>
    Q: <question>
    Response: <response>
    Follow-up Q: <followup question>
    Follow-up Response: <followup response>

The merged content is written into the FIRST Q&A column (its styling is kept) and the now-redundant
Q&A columns are removed — their styling travels with the surviving columns. All OTHER columns, every
cell colour, and other sheets are left untouched.

Run:
    python scripts/reshape_output_a.py "C:\\path\\to\\file.xlsx"
    python scripts/reshape_output_a.py "file.xlsx" --sheet "Output A"
    python scripts/reshape_output_a.py "file.xlsx" --out "copy.xlsx"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _to_bold_rich(text: str):
    """Convert '**x**' markers in a plain string into a CellRichText with bold runs (same as the
    pipeline's _apply_bold). Returns the original string when there are no markers."""
    parts: list = []
    last = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > last:
            parts.append(text[last:m.start()])
        parts.append(TextBlock(InlineFont(b=True), m.group(1)))
        last = m.end()
    if parts and last < len(text):
        parts.append(text[last:])
    return CellRichText(*parts) if parts else text

# Target field -> candidate header names (normalized: lower-cased, single-spaced).
_ALIASES: dict[str, list[str]] = {
    "sno":               ["#", "sno", "s.no", "s no", "sr", "sr no", "serial", "no", "no."],
    "source_file":       ["questionnaire file", "source file", "file name", "file", "source",
                          "document name"],
    # Raw, separate Q&A columns:
    "question":          ["question", "data and report request", "q", "question text"],
    "response":          ["response", "management response", "company response", "answer",
                          "response text"],
    "followup_question": ["follow-up question", "followup question", "follow up question",
                          "followup q", "follow-up q"],
    "followup_response": ["follow-up response", "followup response", "follow up response",
                          "follow-up r"],
    # Already-merged Q&A columns (an earlier Output A layout) — these two get combined too:
    "qr_block":          ["question & response", "question and response"],
    "followup_qr_block": ["follow-up question & response", "follow up question & response",
                          "followup question & response", "follow-up question and response"],
}
_RAW_FIELDS = ("question", "response", "followup_question", "followup_response")
_BLOCK_FIELDS = ("qr_block", "followup_qr_block")
_QA_FIELDS = _RAW_FIELDS + _BLOCK_FIELDS
_MERGED_HEADER = "Questions & Responses"

_RESP_RE = re.compile(r"(?mi)^[ \t]*response[ \t]*:[ \t]*")
_LEAD_Q_RE = re.compile(r"(?mi)^[ \t]*q[ \t]*:[ \t]*")


def _norm(header) -> str:
    return " ".join(str(header or "").strip().lower().split())


def _parse_qr(block: str) -> tuple[str, str]:
    """Parse a pre-merged 'Q: <q> … Response: <r>' cell back into (question, response)."""
    text = (block or "").strip()
    if not text:
        return "", ""
    pieces = _RESP_RE.split(text, maxsplit=1)
    question = _LEAD_Q_RE.sub("", pieces[0], count=1).strip()
    response = pieces[1].strip() if len(pieces) > 1 else ""
    return question, response


def _resolve_headers(ws) -> dict[str, int]:
    """Map each target field -> 1-based column index, by matching the header row (row 1)."""
    nmap: dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        h = _norm(ws.cell(row=1, column=col).value)
        if h and h not in nmap:
            nmap[h] = col
    resolved: dict[str, int] = {}
    for field, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in nmap:
                resolved[field] = nmap[alias]
                break
    return resolved


def _cell_text(ws, row: int, col: int | None) -> str:
    if not col:
        return ""
    v = ws.cell(row=row, column=col).value
    return "" if v is None else str(v).strip()


def _build_merged(ws, row: int, cols: dict[str, int]) -> str:
    """The merged cell for one data row — identical format to output_repo._qa_block."""
    def val(field: str) -> str:
        return _cell_text(ws, row, cols.get(field))

    if cols.get("qr_block") or cols.get("followup_qr_block"):
        question, response = _parse_qr(val("qr_block"))
        followup_q, followup_r = _parse_qr(val("followup_qr_block"))
    else:
        question, response = val("question"), val("response")
        followup_q, followup_r = val("followup_question"), val("followup_response")

    header = f"[{val('sno')}] {val('source_file')}".strip()
    parts: list[str] = [header] if header.strip("[] ") else []
    for label, value in (
        ("Q", question),
        ("Response", response),
        ("Follow-up Q", followup_q),
        ("Follow-up Response", followup_r),
    ):
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reshape an Output A Excel into the merged Q&A column (keeps formatting).")
    ap.add_argument("path", help="Path to the input .xlsx")
    ap.add_argument("--sheet", default="0", help="Sheet name or index to reshape (default: first sheet)")
    ap.add_argument("--out", default=None, help="Output .xlsx path (default: overwrite the input in place)")
    args = ap.parse_args()

    in_path = Path(args.path)
    if not in_path.is_file():
        print(f"ERROR: file not found: {in_path}")
        return 1

    # rich_text=True keeps inline bold (CellRichText runs) that the pipeline writes into Risk
    # Description / Rationale — a plain load would flatten them (losing bold AND the line structure).
    try:
        wb = openpyxl.load_workbook(in_path, rich_text=True)
    except TypeError:  # older openpyxl without the rich_text param
        wb = openpyxl.load_workbook(in_path)
    try:
        ws = wb.worksheets[int(args.sheet)]
    except (ValueError, IndexError):
        ws = wb[args.sheet] if args.sheet in wb.sheetnames else wb.worksheets[0]

    cols = _resolve_headers(ws)
    qa_cols = {f: cols[f] for f in _QA_FIELDS if f in cols}
    if not qa_cols:
        print(f"ERROR: no Question/Response column found on sheet '{ws.title}'.")
        print(f"       Headers seen: {[ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]}")
        return 1

    primary = min(qa_cols.values())                 # keep the leftmost Q&A column, write merged into it
    others = sorted((c for c in qa_cols.values() if c != primary), reverse=True)  # delete the rest (R→L)
    print(f"Reshaping sheet '{ws.title}' | columns -> {cols} | merge into col {primary}, drop {sorted(others)}")

    # Write the merged value into the primary Q&A column (its cell styling is preserved — only .value changes).
    for row in range(2, ws.max_row + 1):
        ws.cell(row=row, column=primary).value = _build_merged(ws, row, cols)
    ws.cell(row=1, column=primary).value = _MERGED_HEADER

    # Remove the now-redundant Q&A columns; surviving columns keep their styles (openpyxl shifts them).
    for c in others:
        ws.delete_cols(c)

    # In EVERY column: turn '**bold**' markers into real bold, and wrap text so bulleted sub-points
    # render on their own lines. Cells already stored as rich text (CellRichText) are left intact.
    for row in range(2, ws.max_row + 1):
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            if isinstance(cell.value, str) and "**" in cell.value:
                cell.value = _to_bold_rich(cell.value)
            a = cell.alignment
            cell.alignment = Alignment(
                horizontal=a.horizontal, vertical="top", wrap_text=True,
                text_rotation=a.text_rotation, indent=a.indent, shrink_to_fit=a.shrink_to_fit,
            )

    # Widen the merged Q&A column for readability.
    ws.column_dimensions[get_column_letter(primary)].width = 80

    out_path = Path(args.out) if args.out else in_path
    wb.save(out_path)
    print(f"Saved -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
