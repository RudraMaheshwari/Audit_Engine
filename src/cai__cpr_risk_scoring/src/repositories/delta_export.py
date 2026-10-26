"""Persist the audit output sheets as Delta tables in Unity Catalog.

After the Excel workbook is written, each reportable sheet is also mirrored to a Delta table in
`<catalog>.<schema>` (default `nbod_gaagd.risk_analysis_output`), suffixed with the audit's engagement
number(s):

    Output A  -> AI_Observations_<eng>
    Output C  -> AI_Risk_Summary_<eng>
    Output E  -> Summary_Past_Reviews_<eng>
    Output F  -> Scope_Transaction_Summary_<eng>
    AI Scope  -> AI_Scope_<eng>

Design choices (agreed): engagement numbers are joined with underscores (e.g. 1000_1001); each run
OVERWRITES its tables (CREATE OR REPLACE); every column is STRING (safest for report content — bullets,
newlines, 'Local / USD' cells, '-' placeholders). Best-effort: any failure logs and never raises into
the audit pipeline (same contract as the KB/index steps).
"""

from __future__ import annotations

import re

from sqlalchemy import text

from src.config.databricks_auth import build_engine, has_credentials
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Canonical Delta column headers for the Output F scope-transaction summary (9-tuple rows).
_SCOPE_TXN_HEADERS = [
    "Scope Area",
    "Transactions - Transactional Tables",
    "Transactions - Supporting Docs",
    "Total Transactions",
    "Amount - Transactional Tables (Local / USD)",
    "Amount - Supporting Docs (Local / USD)",
    "Total Amount (Local / USD)",
    "Transactional Table Source(s)",
    "Supporting-Doc Source(s)",
]

# Risk Analysis output Delta tables: (result label, table base, also has a _copy). Mirrors the
# export_output_a/c/e/f calls — A and C are mirrored with a `_copy` duplicate, E and F are not.
RISK_ANALYSIS_OUTPUT_TABLES = [
    ("output_a", "AI_Observations", True),
    ("output_c", "AI_Risk_Summary", True),
    ("output_e", "Summary_Past_Reviews", False),
    ("output_f", "Scope_Transaction_Summary", False),
]


class DeltaOutputExporter:
    """Writes the audit output datasets to Delta tables via the Databricks SQL warehouse."""

    def __init__(self, settings, schema: str | None = None) -> None:
        self._settings = settings
        self._schema = (schema or getattr(settings, "risk_analysis_output_schema", "") or "risk_analysis_output").strip()
        self._engine = None  # lazy

    # ──────────────────────────────────────────────────────────────────────────
    # Public API — one method per output
    # ──────────────────────────────────────────────────────────────────────────

    def export_output_a(self, rows: list) -> None:
        """Output A (AI Observations) — one row per questionnaire/transactional finding."""
        try:
            from src.repositories.output_repo import _OUTPUT_A_COLUMNS, _cell_value
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Delta] Output A column defs unavailable — skipping | %s", exc)
            return
        headers = [col[1] for col in _OUTPUT_A_COLUMNS]
        data = [[_clean(_cell_value(r, col[0])) for col in _OUTPUT_A_COLUMNS] for r in rows]
        self._write("AI_Observations", headers, data, also_copy=True)

    def export_output_c(self, risks: list, scope_overall: dict) -> None:
        """Output C (AI Risk Summary) — one row per clustered risk."""
        try:
            from src.repositories.output_repo import _OUTPUT_C_COLUMNS, _agg_cell_value
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Delta] Output C column defs unavailable — skipping | %s", exc)
            return
        headers = [col[1] for col in _OUTPUT_C_COLUMNS]
        data = []
        for position, risk in enumerate(risks, start=1):
            try:
                risk.overall_risk_category = scope_overall.get(risk.scope_area, "") or ""
            except Exception:  # noqa: BLE001
                pass
            row = []
            for col in _OUTPUT_C_COLUMNS:
                attr = col[0]
                val = position if attr == "sno" else _agg_cell_value(risk, attr)
                row.append(_clean(val))
            data.append(row)
        self._write("AI_Risk_Summary", headers, data, also_copy=True)

    def export_output_e(self, observations: list) -> None:
        """Output E (Summary of Past Reviews) — one row per extracted observation."""
        try:
            from src.repositories.output_repo import _OUTPUT_E_COLUMNS
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Delta] Output E column defs unavailable — skipping | %s", exc)
            return
        headers = [col[1] for col in _OUTPUT_E_COLUMNS]
        data = []
        for position, obs in enumerate(observations, start=1):
            row = [
                _clean(position if col[0] == "sno" else getattr(obs, col[0], ""))
                for col in _OUTPUT_E_COLUMNS
            ]
            data.append(row)
        self._write("Summary_Past_Reviews", headers, data)

    def export_output_f(self, rows: list) -> None:
        """Output F (Scope Transaction Summary) — the 9-column per-scope summary tuples."""
        data = [[_clean(v) for v in row] for row in rows]
        self._write("Scope_Transaction_Summary", _SCOPE_TXN_HEADERS, data)

    def export_rows(self, table_base: str, headers: list[str], rows: list, also_copy: bool = False) -> None:
        """Generic export for pre-built (headers, string-rows) — used by AI Scope. `also_copy` also
        writes a same-data `<table>_copy` duplicate."""
        data = [[_clean(v) for v in row] for row in rows]
        self._write(table_base, headers, data, also_copy=also_copy)

    # ──────────────────────────────────────────────────────────────────────────
    # Table-path reporting (so the APIs can return where each output landed)
    # ──────────────────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """True when a Databricks warehouse + credentials are available for Delta export."""
        s = self._settings
        return bool(getattr(s, "databricks_warehouse_id", "")) and has_credentials(s)

    def table_fqn(self, table_base: str) -> str:
        """Fully-qualified Delta table name (`catalog.schema.<base>_<engagement>`) for an output base."""
        return (
            f"{self._settings.databricks_catalog}.{self._schema}."
            f"{table_base}_{self._engagement_suffix()}"
        )

    def risk_analysis_output_tables(self) -> dict[str, str]:
        """Map each Risk Analysis output (and its `_copy`) to its Delta table path — the tables the run
        writes when data exists. Empty dict when Delta export is not configured (no warehouse/creds)."""
        if not self.is_configured():
            return {}
        out: dict[str, str] = {}
        for label, base, has_copy in RISK_ANALYSIS_OUTPUT_TABLES:
            out[label] = self.table_fqn(base)
            if has_copy:
                out[f"{label}_copy"] = f"{self.table_fqn(base)}_copy"
        return out

    # ──────────────────────────────────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────────────────────────────────

    def _engagement_suffix(self) -> str:
        nums = getattr(self._settings, "engagement_numbers", ()) or ()
        joined = "_".join(str(n) for n in nums)
        return joined or "unknown"

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        s = self._settings
        if not getattr(s, "databricks_warehouse_id", "") or not has_credentials(s):
            logger.warning("[Delta] No Databricks warehouse/credentials configured — skipping Delta export")
            return None
        # NOTE: do not pin a schema here — the target schema may not exist yet on the first run, and
        # every statement below is fully qualified (catalog.schema.table), so the default is unneeded.
        self._engine = build_engine(s)
        return self._engine

    @staticmethod
    def _safe_columns(headers: list[str]) -> list[str]:
        """Map display headers to unique, valid Delta column identifiers."""
        out: list[str] = []
        seen: set[str] = set()
        for i, h in enumerate(headers):
            name = re.sub(r"[^0-9a-zA-Z]+", "_", str(h)).strip("_").lower() or f"col_{i}"
            if name[0].isdigit():
                name = f"c_{name}"
            base = name
            n = 1
            while name in seen:
                n += 1
                name = f"{base}_{n}"
            seen.add(name)
            out.append(name)
        return out

    def _write(self, table_base: str, headers: list[str], rows: list[list], also_copy: bool = False) -> None:
        """Create-or-replace a STRING-typed Delta table and insert the rows. Best-effort.

        When `also_copy` is set, a same-data duplicate is also written to `<table>_copy`. The copy is
        independently best-effort — a copy failure never affects the main table, and vice versa."""
        if not headers:
            logger.info("[Delta] %s — no columns, skipping", table_base)
            return
        engine = self._get_engine()
        if engine is None:
            return

        table = f"{table_base}_{self._engagement_suffix()}"
        cols = self._safe_columns(headers)
        self._create_table(engine, table, cols, rows)
        if also_copy:
            self._create_table(engine, f"{table}_copy", cols, rows)

    def _create_table(self, engine, table: str, cols: list[str], rows: list[list]) -> None:
        """CREATE OR REPLACE one STRING-typed Delta table and insert the rows. Best-effort — logs and
        never raises, so one table (e.g. a `_copy`) failing cannot abort the others or the pipeline."""
        catalog = self._settings.databricks_catalog
        fq = f"`{catalog}`.`{self._schema}`.`{table}`"
        col_defs = ", ".join(f"`{c}` STRING" for c in cols)

        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{self._schema}`"))
                conn.execute(text(f"CREATE OR REPLACE TABLE {fq} ({col_defs}) USING DELTA"))
                if rows:
                    placeholders = ", ".join(f":p{i}" for i in range(len(cols)))
                    col_list = ", ".join(f"`{c}`" for c in cols)
                    insert_sql = text(f"INSERT INTO {fq} ({col_list}) VALUES ({placeholders})")
                    params = [
                        {f"p{i}": (row[i] if i < len(row) else "") for i in range(len(cols))}
                        for row in rows
                    ]
                    conn.execute(insert_sql, params)
            logger.info("[Delta] Wrote %s | %d row(s), %d col(s)", fq, len(rows), len(cols))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Delta] Export failed for %s — skipping | %s", fq, exc)


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _clean(value) -> str:
    """Stringify a cell for Delta: drop markdown bold markers, normalise None/blank."""
    if value is None:
        return ""
    s = str(value)
    if "**" in s:
        s = _BOLD_RE.sub(r"\1", s)
    return s
