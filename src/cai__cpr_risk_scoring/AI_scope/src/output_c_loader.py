"""Load Output C (the aggregated risk rows) from either the generated Excel workbook or a Delta table.

Only the four fields AI Scope needs are extracted: Scope Area, Likelihood, Impact, Risk Description.
Column headers are matched by a normalised key so both the Excel display headers ("Scope Area",
"Risk Description") and the snake_case DB columns ("scope_area", "risk_description") resolve the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import openpyxl
from sqlalchemy import text
from src.config.constants import SHEET_C_NAME
from src.config.settings import Settings
from src.utils.logger import get_logger

from .config import SOURCE_EXCEL, AIScopeConfig
from .scope_key import normalize

logger = get_logger(__name__)


@dataclass
class RiskRow:
    """One Output C row, reduced to what AI Scope consumes."""

    scope_area: str
    likelihood: float | None
    impact: float | None
    risk_description: str
    risk_rationale: str = ""
    risk_category: str = ""   # Output C per-risk "Risk Category" (authoritative band for this row)


# Normalised header key -> internal field. (normalize() strips spaces/punctuation and lowercases.)
# NOTE: "riskcategory" is the PER-RISK band; it is intentionally distinct from "overallriskcategory"
# (the scope rollup), which AI Scope recomputes itself — so only the per-risk one is mapped here.
_FIELD_BY_KEY = {
    "scopearea": "scope_area",
    "likelihood": "likelihood",
    "impact": "impact",
    "riskdescription": "risk_description",
    "riskrationale": "risk_rationale",
    "riskcategory": "risk_category",
}


def _to_number(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        # Pull the first number out of a noisy cell (e.g. "3 (High)").
        import re
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group()) if m else None


def _rows_from_records(records: list[dict]) -> list[RiskRow]:
    """Map a list of {header: value} dicts (headers already resolved to internal fields) to RiskRows."""
    out: list[RiskRow] = []
    for i, rec in enumerate(records):
        scope = str(rec.get("scope_area") or "").strip()
        if not scope:
            logger.warning("[OutputC] Row %d skipped — blank scope_area (risk_description=%r)", i + 1, str(rec.get("risk_description") or "")[:80])
            continue
        out.append(RiskRow(
            scope_area=scope,
            likelihood=_to_number(rec.get("likelihood")),
            impact=_to_number(rec.get("impact")),
            risk_description=str(rec.get("risk_description") or "").strip(),
            risk_rationale=str(rec.get("risk_rationale") or "").strip(),
            risk_category=str(rec.get("risk_category") or "").strip(),
        ))
    return out


def _resolve_headers(raw_headers: list[str]) -> dict[int, str]:
    """Map column index -> internal field for the headers we care about."""
    mapping: dict[int, str] = {}
    for idx, header in enumerate(raw_headers):
        field = _FIELD_BY_KEY.get(normalize(str(header)))
        if field:
            mapping[idx] = field
    return mapping


def _load_excel(path: Path) -> list[RiskRow]:
    """Load Output C from the workbook — the canonical 'Output C…' sheet, else the 3rd sheet (standard
    layout is Output A, B, C, …)."""
    if not path.exists():
        raise FileNotFoundError(f"Output C workbook not found: {path}")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if SHEET_C_NAME in wb.sheetnames:
            ws = wb[SHEET_C_NAME]
        elif len(wb.worksheets) >= 3:
            ws = wb.worksheets[2]  # 3rd sheet fallback (standard layout: A, B, C, …)
            logger.warning(
                "[OutputC] '%s' sheet not found — falling back to 3rd sheet %r", SHEET_C_NAME, ws.title
            )
        else:
            raise ValueError(f"'{SHEET_C_NAME}' sheet not found and workbook has < 3 sheets")

        all_rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not all_rows:
        logger.warning("[OutputC] Empty sheet — no rows to load")
        return []

    col_field = _resolve_headers([str(h or "") for h in all_rows[0]])
    missing = {"scope_area", "likelihood", "impact", "risk_description"} - set(col_field.values())
    if missing:
        raise ValueError(
            f"Output C sheet is missing required column(s): {sorted(missing)}. "
            f"Found headers: {[h for h in all_rows[0]]}"
        )

    records = [
        {field: row[idx] if idx < len(row) else None for idx, field in col_field.items()}
        for row in all_rows[1:]
    ]
    rows = _rows_from_records(records)
    logger.info("[OutputC] Loaded %d risk row(s) from excel sheet %r", len(rows), ws.title)
    return rows


def _build_engine(settings: Settings):
    from src.config.databricks_auth import build_engine
    return build_engine(settings, schema=settings.databricks_schema_name)


def _load_db_table(settings: Settings, table: str) -> list[RiskRow]:
    engine = _build_engine(settings)
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {table}"))
        raw_cols = list(result.keys())
        fetched = result.fetchall()

    col_field = _resolve_headers([str(c) for c in raw_cols])
    missing = {"scope_area", "likelihood", "impact", "risk_description"} - set(col_field.values())
    if missing:
        raise ValueError(
            f"Delta table {table!r} is missing required column(s): {sorted(missing)}. "
            f"Found columns: {raw_cols}"
        )

    records = [
        {field: row[idx] for idx, field in col_field.items()}
        for row in fetched
    ]
    rows = _rows_from_records(records)
    logger.info("[OutputC] Loaded %d risk row(s) from Delta table %s", len(rows), table)
    return rows


def load_output_c(config: AIScopeConfig) -> list[RiskRow]:
    """Load Output C rows from the configured source (excel workbook or Delta table)."""
    if config.source == SOURCE_EXCEL:
        return _load_excel(Path(config.path))
    return _load_db_table(config.settings, config.path)
