"""Loader for the Master TPI Inventory List.

The workbook holds one sheet per auditor (same column layout), e.g.:
    sam1-4.3, Naeem1-4.3, Naeem2-4.3, Naeem3-4.3, Kaitlyn1-4.3, Kaitlyn2-4.3, Leander1-4.3

Reads the entity-name column from the configured auditor sheet and returns the list of
in-scope third-party names. Used for TPI/supplier name-matching (Phase 3) and for the
classifier's Gate B same-party recognition (injected into the analysis context).
"""

from __future__ import annotations

import re
from typing import Any

import openpyxl

from src.config.constants import (
    TPI_INVENTORY_COL_ENTITY_NAME,
    TPI_INVENTORY_COL_ENTITY_NAME_LOCAL,
    TPI_INVENTORY_ENTITY_HEADER_FALLBACKS,
    TPI_INVENTORY_ENTITY_HEADER_STRONG,
)
from src.config.settings import Settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Header fragments are configured centrally in constants.py (edit there to match a differently-labelled
# inventory). Fallback fragments used only when no strong entity-name column is present.
_ENTITY_HEADER_FALLBACKS = TPI_INVENTORY_ENTITY_HEADER_FALLBACKS


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


# The TWO authoritative TPI entity-name columns (English + Local Language), matched EXACTLY so sibling
# columns that merely CONTAIN "Legal Entity Name" — e.g. "J&J Company/MRC: Legal Entity Name …" or the
# many "Third Party Contact: Legal Entity Name …: Tax ID / DUNs / Address / …" sub-fields — are NOT
# swept in. Configured in constants.py.
_EXACT_ENTITY_HEADERS = {
    TPI_INVENTORY_COL_ENTITY_NAME.strip().lower(),
    TPI_INVENTORY_COL_ENTITY_NAME_LOCAL.strip().lower(),
}
# Specific fragments, used ONLY as a fallback for differently-labelled inventories that lack the exact
# headers above (the content fallback _best_name_column is the final safety net).
_ENTITY_HEADER_STRONG = TPI_INVENTORY_ENTITY_HEADER_STRONG


def _find_entity_columns(header: tuple) -> list[int]:
    """The TPI entity-name column(s) by EXACT match on the configured reference headers (English +
    Local Language) — order-preserving. Exact-only on purpose: sibling columns that merely CONTAIN the
    phrase ("…Legal Entity Name …: Tax ID / DUNs / Address …", "J&J Company … Legal Entity Name …") are
    NOT swept in. Returns [] when neither reference header is present — the caller then detects the
    column(s) schema-agnostically via the LLM (reference is only a hint), never by substring guessing.
    The reference headers live in constants.py; edit THERE for a differently-labelled inventory."""
    cells = [("" if h is None else str(h)).strip() for h in header]
    return [i for i, c in enumerate(cells) if c.lower() in _EXACT_ENTITY_HEADERS]


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


def _detect_entity_columns_via_llm(header: tuple, sample_rows: list, llm: Any) -> list[int]:
    """Schema-agnostic detection of the THIRD PARTY's OWN legal-entity-name column(s) — English and/or
    local language (usually 1-2). Reads the headers + a few sample values, so it works whatever the
    column names are. EXCLUDES contact-person columns, registration / tax / DUNs / PAN / company-ID
    numbers, addresses, dates, ID codes, and the J&J / own-company entity name. The configured reference
    headers are passed only as a HINT. Returns the column indices (≤2), or [] on failure."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        lines = []
        for i, head in enumerate(header):
            head_s = ("" if head is None else str(head)).strip()
            vals = []
            for r in sample_rows[:3]:
                v = r[i] if i < len(r) else ""
                vs = ("" if v is None else str(v)).strip()[:40]
                if vs:
                    vals.append(vs)
            lines.append(f"[{i}] {head_s} = {', '.join(vals)}")
        reference = f"{TPI_INVENTORY_COL_ENTITY_NAME} | {TPI_INVENTORY_COL_ENTITY_NAME_LOCAL}"
        system = (
            "You identify which spreadsheet column(s) hold a Third-Party Intermediary's OWN legal entity "
            "NAME — the name of the third-party company itself, in English and/or the local language. "
            "There are usually one or two such columns. Return ONLY their column indices."
        )
        user = (
            "Columns of a TPI inventory, shown as [index] header = sample values:\n" + "\n".join(lines) +
            f"\n\nReference header names for the target columns (may be labelled differently in this "
            f"file — use only as a hint): {reference}\n\n"
            "Return the index(es) of the column(s) holding the THIRD PARTY's OWN legal entity name "
            "(English and/or local language). EXCLUDE any column that is a contact person's name, a "
            "registration / tax / DUNs / PAN / company-ID number, an address, a date, an ID code, or the "
            "J&J / own-company entity name. Reply with ONLY the indices, comma-separated (e.g. '4,6'); "
            "reply 'none' if there is no such column."
        )
        response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        text = (response.content or "").strip().lower()
        if "none" in text:
            return []
        seen: list[int] = []
        for m in re.findall(r"\d+", text):
            i = int(m)
            if 0 <= i < len(header) and i not in seen:
                seen.append(i)
        return seen[:2]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TPIInventory] LLM multi-column detection failed: %s", exc)
        return []


_HEADER_SCAN_ROWS = 40  # leading rows searched for the real header — some inventories open with a long
                        # title/filter/metadata preamble (10+ rows) before the real column-header row.

# Exact header labels to skip when reading a column's values as names. Matched on the WHOLE cell only —
# a substring match would wrongly drop a real name like 'Vendor Solutions Ltd'.
_HEADER_EXACT = {f.lower() for f in _ENTITY_HEADER_FALLBACKS} | {TPI_INVENTORY_COL_ENTITY_NAME.lower()}


def _is_name_like(val: Any) -> bool:
    """A cell that could be an entity name: non-empty and containing at least one letter (any script,
    so Korean/Japanese names count). Serial numbers, codes and amounts have no letters → excluded."""
    if val is None:
        return False
    s = str(val).strip()
    return bool(s) and any(ch.isalpha() for ch in s)


def _collect_names(rows, col_idx: int) -> list[str]:
    """De-duplicated non-empty values of `col_idx` across `rows`, skipping exact header labels."""
    return _collect_names_multi(rows, [col_idx])


def _collect_names_multi(rows, col_idxs: list[int]) -> list[str]:
    """De-duplicated names gathered ACROSS the given columns (e.g. English + Local Language), in column
    order, skipping exact header labels. Cross-column dedup collapses an English name duplicated into an
    unnamed copy column, while keeping a distinct local-language (e.g. Korean) name as its own entry."""
    names: list[str] = []
    seen: set[str] = set()
    # Diagnostic counters so a 474-in / 398-out gap can be traced to its exact cause.
    raw_nonblank = 0
    dropped_dup = 0
    dropped_header = 0
    per_col: list[str] = []
    for col_idx in col_idxs:
        col_raw = col_dup = col_hdr = col_kept = 0
        for r in rows:
            if col_idx >= len(r):
                continue
            val = r[col_idx]
            if val is None:
                continue
            name = str(val).strip()
            if not name:
                continue
            col_raw += 1
            raw_nonblank += 1
            key = name.lower()
            if key in _HEADER_EXACT:
                dropped_header += 1
                col_hdr += 1
                continue
            if key in seen:
                dropped_dup += 1
                col_dup += 1
                continue
            seen.add(key)
            names.append(name)
            col_kept += 1
        per_col.append(f"col{col_idx}: nonblank={col_raw} kept={col_kept} dup={col_dup} header={col_hdr}")
    logger.info(
        "[TPIInventory] name collection | total_nonblank=%d kept=%d dropped_duplicate=%d "
        "dropped_header=%d | %s",
        raw_nonblank, len(names), dropped_dup, dropped_header, " | ".join(per_col),
    )
    return names


def _best_name_column(rows) -> int | None:
    """Pick the column with the most DISTINCT name-like (alphabetic) values — the entity-name column
    even when no header is recognizable. Distinct count favours the varied name column over a
    low-cardinality column such as Country/Status. Returns None if nothing looks like names."""
    ncols = max((len(r) for r in rows), default=0)
    best_i: int | None = None
    best_count = 0
    for ci in range(ncols):
        vals = {str(r[ci]).strip().lower() for r in rows if ci < len(r) and _is_name_like(r[ci])}
        if len(vals) > best_count:
            best_count, best_i = len(vals), ci
    return best_i


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
        all_rows = [r for r in ws.iter_rows(values_only=True)]
        if not all_rows:
            logger.warning("[TPIInventory] Sheet %r is empty", sheet)
            return []

        # 1) HEADER-ROW SCAN (deterministic): find the header row by an EXACT match on the configured
        # reference headers. Metadata/preamble rows are skipped (they don't carry the exact header). The
        # data starts on the row after it. Both the English AND Local-Language name columns are captured.
        header_idx: int | None = None
        col_idxs: list[int] = []
        for i, row in enumerate(all_rows[:_HEADER_SCAN_ROWS]):
            cis = _find_entity_columns(row)
            if cis:
                header_idx, col_idxs = i, cis
                break

        # 2) LLM FALLBACK (schema-agnostic): when the reference headers are NOT present (a differently-
        # labelled inventory), ask the LLM which column(s) hold the third party's own entity name,
        # giving it the header row + a few sample rows (reference headers are only a hint). Never a
        # substring guess. Pick the header row as the first leading row with >=2 non-empty cells.
        if not col_idxs and llm is not None:
            hdr_i = next(
                (i for i, row in enumerate(all_rows[:_HEADER_SCAN_ROWS])
                 if sum(1 for c in row if c not in (None, "")) >= 2),
                0,
            )
            samples = all_rows[hdr_i + 1: hdr_i + 1 + 5]
            cis = _detect_entity_columns_via_llm(all_rows[hdr_i], samples, llm)
            if cis:
                header_idx, col_idxs = hdr_i, cis
                detected = [str(all_rows[hdr_i][c]) for c in cis if c < len(all_rows[hdr_i])]
                logger.info(
                    "[TPIInventory] Entity column(s) detected via LLM | indexes=%s | headers=%s",
                    cis, detected,
                )

        # Extract from the chosen column(s) (rows strictly after the header row).
        names: list[str] = []
        if col_idxs:
            start = (header_idx + 1) if header_idx is not None else 0
            names = _collect_names_multi(all_rows[start:], col_idxs)
            if len(col_idxs) > 1:
                logger.info(
                    "[TPIInventory] Captured entity names from %d columns (English + local language) "
                    "| indexes=%s", len(col_idxs), col_idxs,
                )

        # 3) CONTENT FALLBACK: if the chosen column yielded nothing (e.g. the LLM picked an empty/serial
        # column), pick the column with the most DISTINCT name-like values and read that instead.
        if not names:
            ci = _best_name_column(all_rows)
            if ci is not None:
                names = _collect_names(all_rows, ci)
                if names:
                    header_idx, col_idxs = None, [ci]
                    logger.info(
                        "[TPIInventory] Entity column resolved by content fallback | index=%d", ci,
                    )

        if not names:
            logger.warning(
                "[TPIInventory] No entity names found in sheet %r | first_row=%s", sheet, all_rows[0],
            )
            return []

        # Describe the column(s) picked (header label when known) + which leading row was the header,
        # since column detection scans the first few rows. Plus a sample of the first 5 names loaded.
        def _col_label(ci: int) -> str:
            if header_idx is not None and ci < len(all_rows[header_idx]):
                h = all_rows[header_idx][ci]
                return f"[{ci}] {('' if h is None else str(h)).strip() or '(no header)'}"
            return f"[{ci}] (content-detected)"

        cols_desc = ", ".join(_col_label(ci) for ci in col_idxs) or "(none)"
        header_row_desc = f"row {header_idx + 1}" if header_idx is not None else "content fallback"
        # Sample shows BOTH ends — the English column is read first and the local-language column last,
        # so first-3 + last-3 surfaces names in both languages (confirming both were captured).
        sample = (names[:3] + names[-3:]) if len(names) > 6 else names
        logger.info(
            "[TPIInventory] Loaded %d entity names | sheet=%s | columns picked (%s): %s | sample=%s | file=%s",
            len(names), sheet, header_row_desc, cols_desc, sample, path,
        )
        # When BOTH the English AND the Local-Language columns were captured, we already hold the names
        # in both languages — so SKIP the Azure Translator augmentation (it is a slow second pass and
        # would only add machine-translated variants we don't need). Augment only when a SINGLE column
        # was found (then a translation helps match a ledger in the other language).
        if len(col_idxs) > 1:
            logger.info("[TPIInventory] Both English + local-language columns captured — "
                        "skipping English augmentation (no translation pass needed)")
            return names
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
