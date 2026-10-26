"""Loader for the Master TPI Inventory List.

The workbook holds one sheet per auditor (same column layout), e.g.:
    sam1-4.3, Naeem1-4.3, Naeem2-4.3, Naeem3-4.3, Kaitlyn1-4.3, Kaitlyn2-4.3, Leander1-4.3

Reads the entity-name column from the configured auditor sheet and returns the list of
in-scope third-party names. Used for TPI/supplier name-matching (Phase 3) and for the
classifier's Gate B same-party recognition (injected into the analysis context).
"""

from __future__ import annotations

import itertools
import re
from typing import Any

import openpyxl

from src.config.constants import TPI_INVENTORY_COL_ENTITY_NAME
from src.config.settings import Settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Fallback header fragments if the exact column name is not present.
_ENTITY_HEADER_FALLBACKS = ("legal entity name", "entity name", "third party", "vendor", "supplier", "name")


def _norm(s: str) -> str:
    """Normalise for matching: lowercase, keep only letters/digits."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _sheet_base(sheet: str) -> str:
    """Strip a trailing version suffix like '-4.3' so 'Leander1-4.3' -> 'Leander1'."""
    return re.sub(r"[-_\s]*\d+(?:\.\d+)+\s*$", "", sheet)


def resolve_auditor_sheet(inventory_path: str, auditor_id: str) -> str | None:
    """Pick the workbook sheet for the given auditor (e.g. 'Sam_1' -> 'sam1-4.3',
    'Leander_1' -> 'Leander1-4.3'). Returns None if the file can't be opened or no sheet matches."""
    if not auditor_id:
        return None
    try:
        wb = openpyxl.load_workbook(inventory_path, read_only=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TPIInventory] Could not open %s to resolve auditor sheet: %s", inventory_path, exc)
        return None
    try:
        sheetnames = list(wb.sheetnames)
    finally:
        wb.close()

    target = _norm(auditor_id)
    if not target:
        return None
    # 1) exact match on the sheet base (before the version suffix).
    for sheet in sheetnames:
        if _norm(_sheet_base(sheet)) == target:
            return sheet
    # 2) fallback: the auditor id is contained in the normalised sheet name (or vice versa).
    for sheet in sheetnames:
        ns = _norm(sheet)
        if target and (target in ns or _norm(_sheet_base(sheet)).startswith(target)):
            return sheet
    logger.warning(
        "[TPIInventory] No sheet matched auditor '%s' | available=%s", auditor_id, sheetnames,
    )
    return None


def _find_entity_column(header: tuple) -> int | None:
    cells = [("" if h is None else str(h)).strip() for h in header]
    # Exact match first.
    for i, c in enumerate(cells):
        if c == TPI_INVENTORY_COL_ENTITY_NAME:
            return i
    # Fallback: fuzzy match on known fragments, best (earliest fragment) wins.
    for frag in _ENTITY_HEADER_FALLBACKS:
        for i, c in enumerate(cells):
            if frag in c.lower():
                return i
    return None


def _detect_entity_column_via_llm(header: tuple, sample_row: tuple, llm: Any) -> int | None:
    """Ask the LLM which column holds the TPI name, given the headers + one sample row.

    Schema-agnostic fallback for inventories whose column names we can't predict. Returns the column
    index or None (on failure), so the caller degrades gracefully.
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        lines = []
        for i, head in enumerate(header):
            value = sample_row[i] if sample_row and i < len(sample_row) else ""
            head_s = ("" if head is None else str(head)).strip()
            value_s = ("" if value is None else str(value)).strip()[:80]
            lines.append(f"[{i}] {head_s} = {value_s}")

        system = (
            "You identify which column of a spreadsheet holds the third party's entity name. "
            "Answer with a single integer column index only."
        )
        user = (
            "Below are the column headers of a Third-Party Intermediary (TPI) inventory and one "
            "sample row, shown as [index] header = value:\n" + "\n".join(lines) +
            "\n\nWhich single column contains the third party's NAME — the entity / vendor / supplier "
            "/ party name used to identify the TPI? Reply with ONLY the column index number."
        )
        response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        match = re.search(r"\d+", (response.content or ""))
        if not match:
            return None
        idx = int(match.group())
        return idx if 0 <= idx < len(header) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TPIInventory] LLM column detection failed: %s", exc)
        return None


def load_tpi_inventory_names(
    settings: Settings, sheet: str | None = None, llm: Any = None
) -> list[str]:
    """Return the de-duplicated list of TPI entity names from the given (or configured) sheet.

    Column detection is schema-agnostic: exact/keyword header match first, then — if that fails and an
    `llm` is supplied — an LLM fallback that reads the headers plus one sample row. Returns an empty
    list (and logs a warning) if the file, sheet, or column is missing — the pipeline degrades
    gracefully rather than failing.
    """
    path = settings.tpi_inventory_file_path
    sheet = sheet or settings.tpi_inventory_sheet
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TPIInventory] Could not open %s: %s", path, exc)
        return []
    try:
        if sheet not in wb.sheetnames:
            logger.warning(
                "[TPIInventory] Sheet %r not found in %s | available=%s",
                sheet, path, wb.sheetnames,
            )
            return []
        ws = wb[sheet]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            logger.warning("[TPIInventory] Sheet %r is empty", sheet)
            return []
        first_data = next(rows, None)

        col_idx = _find_entity_column(header)
        if col_idx is None and llm is not None and first_data is not None:
            col_idx = _detect_entity_column_via_llm(header, first_data, llm)
            if col_idx is not None:
                detected = header[col_idx] if col_idx < len(header) else "?"
                logger.info(
                    "[TPIInventory] Entity column detected via LLM | index=%d | header=%r",
                    col_idx, detected,
                )
        if col_idx is None:
            logger.warning(
                "[TPIInventory] Entity column not found in sheet %r | header=%s", sheet, header,
            )
            return []

        data_rows = rows if first_data is None else itertools.chain([first_data], rows)
        names: list[str] = []
        seen: set[str] = set()
        for r in data_rows:
            if col_idx >= len(r):
                continue
            val = r[col_idx]
            if val is None:
                continue
            name = str(val).strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
        logger.info(
            "[TPIInventory] Loaded %d entity names | sheet=%s | file=%s",
            len(names), sheet, path,
        )
        return _augment_with_english(settings, names, llm)
    finally:
        wb.close()


def _augment_with_english(settings: Settings, names: list[str], llm: Any) -> list[str]:
    """Add an English translation for every non-English TPI name, KEEPING the original too.

    TPI names are matched against the transactional SUPPLIER_NAME. We don't know whether the ledger
    carries the original-language name or a romanised/English one, so we keep BOTH the original and its
    English translation in the list — matching then succeeds regardless of the ledger's language.
    Best-effort: returns the originals unchanged on any failure or when translation is disabled."""
    if not names:
        return names
    try:
        from src.services.translation import TranslationService

        out = TranslationService(settings, llm=llm).augment_with_english(names)
        if len(out) != len(names):
            logger.info(
                "[TPIInventory] Added English variants for non-English names | %d -> %d entries",
                len(names), len(out),
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TPIInventory] English augmentation skipped — keeping originals | %s", exc)
        return names


def format_tpi_inventory_block(names: list[str], sheet: str) -> str:
    """Format the TPI inventory names as a context block for the classifier/analysis prompt."""
    if not names:
        return ""
    joined = "\n".join(f"- {n}" for n in names)
    return (
        "=== TPI INVENTORY — IN-SCOPE THIRD PARTIES (due diligence completed) "
        f"[auditor sheet: {sheet}] ===\n"
        "Match named parties / suppliers / payees against this list. Treat COSMETIC differences as "
        "the SAME entity (legal suffixes like Pty Ltd/Ltd/Inc, punctuation, case, spacing, "
        "ampersands, 't/a' trading names) — e.g. 'Ethical Strategies Pty Ltd' = 'Ethical "
        "Strategies'. But an EXTRA distinguishing word/acronym that is NOT a legal suffix means a "
        "DIFFERENT (affiliate/child) entity — e.g. 'MSA Pharma' ≠ 'MSA Pharma IMM' — do NOT "
        "treat those as the same. A party that COSMETICALLY matches an entry below is an in-scope "
        "TPI with due diligence completed.\n"
        f"{joined}"
    )
