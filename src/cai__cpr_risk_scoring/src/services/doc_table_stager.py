"""Stage supporting-doc tabular data as TEMPORARY tables in a LOCAL SQLite database for Phase 3.

Some transactional evidence lives not in the dashboard tables but in auditor supporting documents — the
IDR Tender Report, the PSP list, the TPI inventory, distributor lists, etc. Phase 3 reads each such doc,
INFERS its column types, and loads it into a table in a throwaway local SQLite file (in the OS temp
folder) — so no Databricks catalog/schema permissions are needed. The staged tables can be queried
locally via `query()`; the whole SQLite file is deleted when Phase 3 finishes (context manager or
`drop_all()` in a `finally`), so nothing is left behind.

    with DocTableStager(settings, run_id) as stager:
        tbl = stager.stage("TENDERS", headers, rows)        # -> local table name 'tmp_tenders_<run>'
        rows = stager.query(f"SELECT COUNT(*) AS n FROM {tbl}")
    # the local SQLite file is deleted here

NOTE: these are LOCAL tables — the Databricks per-scope calc SQL cannot JOIN them (different engine).
They are for local computation only; the caller does not inject them into the Databricks prompt.
"""

from __future__ import annotations

import re
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from src.utils.logger import get_logger

logger = get_logger(__name__)

_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]+")
# Date formats accepted when inferring/parsing a DATE column (ISO first, then common sheet formats).
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S")
_MAX_COLS = 60            # guardrail: ignore absurdly wide sheets
_INSERT_BATCH = 500       # rows per executemany batch
# Inferred type -> SQLite column type (affinity): STRING/DATE as TEXT, DOUBLE as REAL.
_SQLITE_TYPE = {"DOUBLE": "REAL", "DATE": "TEXT", "STRING": "TEXT"}


def _safe_ident(name: str, fallback: str) -> str:
    """Sanitise a header into a valid, lowercase SQL identifier (letters/digits/underscore)."""
    s = _IDENT_RE.sub("_", (name or "").strip().lower()).strip("_")
    if not s or s[0].isdigit():
        s = f"c_{s}" if s else fallback
    return s[:120]


# Alpha currency codes stripped when parsing an amount cell (e.g. "USD 571,620" -> 571620).
_CCY_CODE_RE = re.compile(r"\b(?:USD|EUR|GBP|AUD|NZD|CAD|JPY|KRW|CNY|INR)\b", re.IGNORECASE)


def _num(value: Any) -> float | None:
    """Parse a cell as a number, tolerating thousands separators, currency symbols AND alpha currency
    codes (USD/EUR/…). None if not numeric — so a column with any textual value stays STRING."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    s = _CCY_CODE_RE.sub("", s)
    s = re.sub(r"[,$€£¥\s]", "", s)
    s = re.sub(r"[()]", "", s)  # ignore parentheses (negatives are rare here and out of scope)
    try:
        return float(s)
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    """Parse a cell as a date (accepts date/datetime objects and the common string formats). None if not."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _infer_type(values: list[Any]) -> str:
    """Infer a column's type from its non-empty values: DOUBLE if all numeric, DATE if all dates, else
    STRING. Conservative — a single non-conforming value falls back to STRING so nothing is lost."""
    seen = [v for v in values if v not in (None, "")]
    if not seen:
        return "STRING"
    if all(_num(v) is not None for v in seen):
        return "DOUBLE"
    if all(_as_date(v) is not None for v in seen):
        return "DATE"
    return "STRING"


def _short_err(exc: Exception) -> str:
    """One-line error for logging — no multi-line SQL dump."""
    return str(exc).splitlines()[0][:220]


class DocTableStager:
    """Creates typed tables from supporting-doc (headers, rows) in a LOCAL SQLite file; deletes it on
    teardown. Same API as the previous Databricks stager, plus `query()` for local reads."""

    def __init__(self, settings: Any = None, run_id: str | None = None) -> None:
        self._settings = settings
        # Short, unique run tag so concurrent runs never collide on a file/table name.
        self._run_tag = _safe_ident((run_id or uuid.uuid4().hex)[:8], "run")
        self._db_path = Path(tempfile.gettempdir()) / f"phase3_stage_{self._run_tag}.sqlite"
        self._engine = None
        self._staged: dict[str, str] = {}          # key -> local table name
        self._columns: dict[str, list[str]] = {}   # table name -> its column identifiers

    def __enter__(self) -> "DocTableStager":
        return self

    def __exit__(self, *exc) -> None:
        self.drop_all()

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine(f"sqlite:///{self._db_path}")
        return self._engine

    @property
    def staged_tables(self) -> dict[str, str]:
        """{key: local table name} for every table successfully staged."""
        return dict(self._staged)

    def columns_for(self, table: str) -> list[str]:
        """Column identifiers of a staged table (empty if unknown)."""
        return list(self._columns.get(table, []))

    def query(self, sql: str) -> list[dict]:
        """Run a read query against the local staged DB; returns a list of row dicts ([] on failure)."""
        try:
            with self._get_engine().connect() as conn:
                res = conn.execute(text(sql))
                keys = list(res.keys())
                return [dict(zip(keys, row)) for row in res.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DocStager] query failed | %s", _short_err(exc))
            return []

    @staticmethod
    def _column_idents(headers: list[str]) -> list[str]:
        """Sanitised, de-duplicated column identifiers aligned to the header positions."""
        cols: list[str] = []
        used: set[str] = set()
        for i, h in enumerate(headers[:_MAX_COLS]):
            ident = _safe_ident(h, f"col_{i}")
            base, n = ident, 2
            while ident in used:
                ident, n = f"{base}_{n}", n + 1
            used.add(ident)
            cols.append(ident)
        return cols

    def _create_and_load(self, table_key: str, headers: list[str], rows: list[list]):
        """Create local table `tmp_<table_key>_<run>` with inferred column types + load rows. Returns
        (table_name, cols, col_types). Records it under `table_key` for teardown. Raises on failure."""
        cols = self._column_idents(headers)
        ncols = len(cols)
        col_values = [[(r[i] if i < len(r) else None) for r in rows] for i in range(ncols)]
        col_types = [_infer_type(vals) for vals in col_values]
        table = f"tmp_{_safe_ident(table_key, 'doc')}_{self._run_tag}"
        col_defs = ", ".join(f"`{c}` {_SQLITE_TYPE[t]}" for c, t in zip(cols, col_types))
        engine = self._get_engine()
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
            conn.execute(text(f"CREATE TABLE `{table}` ({col_defs})"))
            self._staged[table_key] = table   # record NOW so a mid-insert failure still gets cleaned up
            self._insert_rows(conn, table, cols, col_types, rows)
        self._columns[table] = cols
        return table, cols, col_types

    def stage(self, key: str, headers: list[str], rows: list[list], *, source_name: str = "") -> str | None:
        """Create a local table from (headers, rows) with inferred column types and load the rows.
        Returns the table name, or None if there is nothing usable to stage. Best-effort."""
        if not headers or not rows:
            logger.info("[DocStager] %s — no tabular rows to stage (source=%s)", key, source_name or "?")
            return None
        try:
            table, cols, col_types = self._create_and_load(key, headers, rows)
            logger.info(
                "[DocStager] Staged %s -> %s | cols=%d | rows=%d | types=%s | source=%s",
                key, table, len(cols), len(rows), dict(zip(cols, col_types)), source_name or "?",
            )
            return table
        except Exception as exc:  # noqa: BLE001 — never break Phase 3; fall back to no staged table
            logger.warning("[DocStager] Failed to stage %s (source=%s) | %s",
                           key, source_name or "?", _short_err(exc))
            return None

    def stage_workbook(self, key: str, sheets: list[tuple[str, list[str], list[list]]],
                       *, source_name: str = "") -> str | None:
        """Stage a MULTI-SHEET workbook (e.g. the TPI inventory) as ONE merged local table. Cross-checks
        the sheets, auto-detects a shared key column, and LEFT JOINs them on it. Returns the merged
        table name (or the single sheet's name when there is only one usable sheet). None if nothing is
        stageable. Best-effort."""
        usable = [(n, h, r) for (n, h, r) in sheets if h and r]
        if not usable:
            logger.info("[DocStager] %s — workbook has no tabular sheets (source=%s)", key, source_name or "?")
            return None
        if len(usable) == 1:
            n, h, r = usable[0]
            return self.stage(key, h, r, source_name=f"{source_name}:{n}" if source_name else n)

        try:
            staged: list[tuple[str, str, list[str]]] = []   # (sheet_name, table, cols)
            for i, (sheet_name, h, r) in enumerate(usable):
                table, cols, _types = self._create_and_load(f"{key}_s{i}", h, r)
                staged.append((sheet_name, table, cols))
                logger.info("[DocStager] %s — sheet %r staged -> %s | cols=%s | rows=%d",
                            key, sheet_name, table, cols, len(r))

            join_key = self._detect_join_key([cols for _n, _t, cols in staged])
            if not join_key:
                logger.warning(
                    "[DocStager] %s — no shared key across sheets %s; kept %d per-sheet tables. source=%s",
                    key, [n for n, _t, _c in staged], len(staged), source_name or "?",
                )
                return staged[0][1]

            merged = f"tmp_{_safe_ident(key, 'doc')}_merged_{self._run_tag}"
            select_sql, merged_cols = self._build_merge_select(staged, join_key)
            engine = self._get_engine()
            with engine.begin() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS `{merged}`"))
                conn.execute(text(f"CREATE TABLE `{merged}` AS {select_sql}"))
            self._staged[f"{key}__merged"] = merged
            self._columns[merged] = merged_cols
            logger.info("[DocStager] %s — merged %d sheets on key %r -> %s | source=%s",
                        key, len(staged), join_key, merged, source_name or "?")
            return merged
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DocStager] Failed to stage workbook %s (source=%s) | %s",
                           key, source_name or "?", _short_err(exc))
            return self._staged.get(f"{key}_s0")

    @staticmethod
    def _detect_join_key(per_sheet_cols: list[list[str]]) -> str | None:
        """A column identifier present in EVERY sheet, preferring a name/id-like key. None if none shared."""
        common = set(per_sheet_cols[0])
        for cols in per_sheet_cols[1:]:
            common &= set(cols)
        if not common:
            return None
        preferred = ("name", "entity", "tpi", "vendor", "supplier", "id")
        for col in per_sheet_cols[0]:
            if col in common and any(p in col for p in preferred):
                return col
        for col in per_sheet_cols[0]:
            if col in common:
                return col
        return None

    @staticmethod
    def _build_merge_select(staged: list[tuple[str, str, list[str]]], join_key: str) -> tuple[str, list[str]]:
        """LEFT JOIN all staged sheet tables on `join_key`, selecting the key once plus every other
        column (collisions across sheets dropped — first sheet wins). Returns (select_sql, merged_cols)."""
        _base_name, base_table, base_cols = staged[0]
        seen = {join_key}
        merged_cols = [join_key]
        select_parts = [f"t0.`{join_key}` AS `{join_key}`"]
        for c in base_cols:
            if c not in seen:
                seen.add(c)
                merged_cols.append(c)
                select_parts.append(f"t0.`{c}` AS `{c}`")
        from_parts = [f"`{base_table}` t0"]
        for i, (_name, table, cols) in enumerate(staged[1:], start=1):
            alias = f"t{i}"
            from_parts.append(f"LEFT JOIN `{table}` {alias} ON {alias}.`{join_key}` = t0.`{join_key}`")
            for c in cols:
                if c not in seen:
                    seen.add(c)
                    merged_cols.append(c)
                    select_parts.append(f"{alias}.`{c}` AS `{c}`")
        return "SELECT " + ", ".join(select_parts) + " FROM " + " ".join(from_parts), merged_cols

    def _insert_rows(self, conn, table: str, cols: list[str], col_types: list[str], rows: list[list]) -> None:
        placeholders = ", ".join(f":{c}" for c in cols)
        insert_sql = text(f"INSERT INTO `{table}` (" + ", ".join(f"`{c}`" for c in cols) + f") VALUES ({placeholders})")
        batch: list[dict] = []
        for r in rows:
            record = {}
            for i, (c, t) in enumerate(zip(cols, col_types)):
                raw = r[i] if i < len(r) else None
                if raw in (None, ""):
                    record[c] = None
                elif t == "DOUBLE":
                    record[c] = _num(raw)
                elif t == "DATE":
                    d = _as_date(raw)
                    record[c] = d.isoformat() if d else None   # SQLite stores dates as ISO text
                else:
                    record[c] = str(raw).strip()
            batch.append(record)
            if len(batch) >= _INSERT_BATCH:
                conn.execute(insert_sql, batch)
                batch = []
        if batch:
            conn.execute(insert_sql, batch)

    def drop_all(self) -> None:
        """Drop every staged table and DELETE the local SQLite file (idempotent, best-effort)."""
        if self._engine is not None:
            try:
                self._engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
        try:
            if self._db_path.exists():
                self._db_path.unlink()
                logger.info("[DocStager] Deleted local staging DB %s (%d table(s))",
                            self._db_path.name, len(self._staged))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DocStager] Could not delete staging DB %s | %s", self._db_path, exc)
        finally:
            self._staged.clear()
            self._columns.clear()
