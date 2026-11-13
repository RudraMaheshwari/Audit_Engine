"""One-shot cross-check for the Output C calibration doubt (rows 26 / 19 / 15).

Throwaway diagnostic. Reuses Settings so there are NO hardcoded engagement numbers or table
names — the engagement predicagte and the HCC / Disbursement table names come from config. It
introspects each table's real schema, auto-detects the PO-flag and amount columns, prints exactly
which columns/SQL it used, and runs the three checks in one run.

Run:  python diagnostic_output_c_calibration.py
"""

from __future__ import annotations

import re

from sqlalchemy import create_engine, text

from src.config.constants import DATABRICKS_WAREHOUSE_PATH_PREFIX
from src.config.settings import Settings


def build_engine(settings: Settings):
    host = settings.databricks_host.replace("https://", "")
    uri = (
        f"databricks://token:{settings.databricks_token}@{host}"
        f"?http_path={DATABRICKS_WAREHOUSE_PATH_PREFIX}{settings.databricks_warehouse_id}"
        f"&catalog={settings.databricks_catalog}"
        f"&schema={settings.databricks_schema_name}"
    )
    return create_engine(uri, pool_pre_ping=True)


def run_sql(engine, sql: str) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]


def columns(engine, table: str) -> list[str]:
    out: list[str] = []
    for r in run_sql(engine, f"DESCRIBE {table}"):
        name = (r.get("col_name") or "").strip()
        if name and not name.startswith("#"):
            out.append(name)
    return out


def pick(cols: list[str], *needles: str) -> str | None:
    """First column whose lowercased name contains ALL needles."""
    for c in cols:
        lc = c.lower()
        if all(n in lc for n in needles):
            return c
    return None


def show(rows: list[dict]) -> None:
    if not rows:
        print("   (no rows)")
        return
    for i, row in enumerate(rows, 1):
        parts = [f"{k}={v}" for k, v in row.items()]
        print(f"   {i}. " + " | ".join(parts))


def main() -> None:
    settings = Settings()
    eng = build_engine(settings)

    where = settings.engagement_filter_clause  # e.g. "ENGAGEMENT_NUMBER IN (1000, 1001)"
    tables = settings.dashboard_tables          # {label: fully-qualified table}
    hcc = tables.get("hcc")
    disb = tables.get("disbursement")

    print(f"Engagement predicate : {where}")
    print(f"HCC table            : {hcc}")
    print(f"Disbursement table   : {disb}\n")

    # Resolve real column names from the schemas.
    disb_cols = columns(eng, disb) if disb else []
    hcc_cols = columns(eng, hcc) if hcc else []

    po_col = pick(disb_cols, "po", "flag") or pick(disb_cols, "po", "number") or pick(disb_cols, "purchase", "order")
    amt_col = (pick(disb_cols, "invoice", "amt", "usd") or pick(disb_cols, "invoice", "amount", "usd")
               or pick(disb_cols, "amount", "usd") or pick(disb_cols, "amt", "usd"))
    gl_col = pick(disb_cols, "gl", "account", "code") or "GL_ACCOUNT_CODE"
    gov_col = pick(disb_cols, "government", "vendor") or "GOVERNMENT_VENDORS"
    sup_col = pick(disb_cols, "supplier", "name") or "SUPPLIER_NAME"
    stage_col = pick(hcc_cols, "stage", "status") or "STAGE_STATUS"

    print(f"Detected → po_flag={po_col} | amount={amt_col} | gl={gl_col} | gov={gov_col} | supplier={sup_col} | stage={stage_col}\n")

    # ── Check 26: is the HCC "closure/stage" signal an unfed column? ──────────────
    print("=" * 70)
    print("CHECK 26 (HCC_MONITORING) — is the driving column unfed?")
    print("=" * 70)
    try:
        sql26 = (f"SELECT COUNT(*) total, COUNT(`{stage_col}`) stage_nonnull "
                 f"FROM {hcc} WHERE {where}")
        print("SQL:", sql26)
        show(run_sql(eng, sql26))
        print("→ stage_nonnull ≈ 0 (or « total) confirms UNFED column → No Risk, not a gap.")
    except Exception as exc:
        print("  ERROR:", exc)

    # ── Check 19: does a non-PO signal survive after excluding statutory GLs? ─────
    print("\n" + "=" * 70)
    print("CHECK 19 (PROCUREMENT_FROM_GOEs) — non-PO signal on GOEs, statutory GLs excluded")
    print("=" * 70)
    if not po_col:
        print("  Could not auto-detect a PO column; available disbursement columns:")
        print("  ", ", ".join(disb_cols))
    else:
        try:
            sql19 = (f"SELECT `{gov_col}` gov, COUNT(*) txns, "
                     f"SUM(CASE WHEN `{po_col}` IS NULL OR TRIM(`{po_col}`)='' THEN 1 ELSE 0 END) no_po "
                     f"FROM {disb} WHERE {where} "
                     f"AND `{gov_col}` IN ('Hospital','University','Clinic') "
                     f"AND LEFT(`{gl_col}`,2) NOT IN ('21','36') "
                     f"GROUP BY `{gov_col}`")
            print("SQL:", sql19)
            show(run_sql(eng, sql19))
            print("→ meaningful no_po counts on real GOEs → residual may support Potential Risk (Low);")
            print("  near-zero after exclusion → Further Review Required (drops out).")
        except Exception as exc:
            print("  ERROR:", exc)

    # ── Check 15: real equipment-placement residual after stripping statutory payees ─
    print("\n" + "=" * 70)
    print("CHECK 15 (EQUIPMENT_PLACEMENT) — hospital residual, statutory GLs excluded")
    print("=" * 70)
    if not amt_col:
        print("  Could not auto-detect an amount column; available disbursement columns:")
        print("  ", ", ".join(disb_cols))
    else:
        try:
            sql15 = (f"SELECT `{sup_col}` supplier, COUNT(*) txns, "
                     f"ROUND(SUM(try_cast(`{amt_col}` AS DOUBLE)),2) usd "
                     f"FROM {disb} WHERE {where} "
                     f"AND `{gov_col}`='Hospital' "
                     f"AND LEFT(`{gl_col}`,2) NOT IN ('21','36') "
                     f"GROUP BY `{sup_col}` ORDER BY txns DESC LIMIT 10")
            print("SQL:", sql15)
            show(run_sql(eng, sql15))
            print("→ a genuine, sizeable equipment-placement residual → Potential Risk (Low);")
            print("  only tiny/scattered rows → Further Review Required (drops out).")
        except Exception as exc:
            print("  ERROR:", exc)


if __name__ == "__main__":
    main()
