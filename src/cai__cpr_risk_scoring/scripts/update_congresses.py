"""Surgically update ONLY the Congresses & JJ Organized Events rows in an existing phase-3 workbook.

Runs Phase-3 analysis for the single CONGRESSES__JJ_ORGANIZED_EVENTS scope and rewrites just that
scope's row(s) in Output A (Risk Detail) and its row in Output F (Scope Txn Summary) IN PLACE — every
other scope and every other sheet is left untouched. Use this after the apostrophe/punctuation calc fix
to refresh Congresses without re-running the full 48-scope pipeline.

Run (same env as run_phase3_outputs.py):
    python scripts/update_congresses.py --out outputs/phase3_outputs.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv((_ROOT / ".env") if (_ROOT / ".env").exists() else find_dotenv(usecwd=True))


def _fix_tls() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass


_fix_tls()

from src.agents.transactional_data_analysis_agent import TransactionalDataAnalysisAgent  # noqa: E402
from src.config.constants import SCOPE_ID_TO_DISPLAY_NAME, SHEET_F_NAME  # noqa: E402
from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider  # noqa: E402
from src.output.output_writer import OutputWriter  # noqa: E402
from src.pipeline.runner import _prepare_reference_files  # noqa: E402
from src.repositories.output_repo import OutputRepository  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("update_congresses")

SCOPE_ID = "CONGRESSES__JJ_ORGANIZED_EVENTS"
_SCOPE_AREA_COL_A = 6   # Output A: "Scope Area" is the 6th column (see _OUTPUT_A_COLUMNS)


def _amount_cell(usd_by_date: dict, usd_total: float, present: bool) -> str:
    if not present:
        return "-"
    if not usd_by_date and not usd_total:
        return "-"
    return f"USD {usd_total:,.2f}"


def _output_f_row(display: str, summary) -> tuple:
    """Replicate build_output_f_rows' per-scope 9-tuple for one summary."""
    table_present = summary.txn_count > 0 or bool(summary.usd_by_date)
    doc_present = summary.doc_txn_count > 0 or bool(summary.doc_usd_by_date)
    table_txns = summary.txn_count if table_present else "-"
    doc_txns = summary.doc_txn_count if doc_present else "-"
    total_txns = (summary.txn_count + summary.doc_txn_count) if (table_present or doc_present) else "-"
    table_amt = _amount_cell(summary.usd_by_date, summary.total_usd, table_present)
    doc_amt = _amount_cell(summary.doc_usd_by_date, summary.doc_total_usd, doc_present)
    override = getattr(summary, "amount_display_override", None)
    if override and table_present:
        table_amt = override
    if override and table_present and not doc_present:
        total_amt = override
    elif table_present or doc_present:
        combined: dict = {}
        for d, u in list(summary.usd_by_date.items()) + list(summary.doc_usd_by_date.items()):
            combined[d] = combined.get(d, 0.0) + u
        total_amt = _amount_cell(combined, summary.total_usd + summary.doc_total_usd, True)
    else:
        total_amt = "-"
    return (
        display, table_txns, doc_txns, total_txns, table_amt, doc_amt, total_amt,
        "; ".join(summary.table_sources) if summary.table_sources else "-",
        "; ".join(summary.doc_sources) if summary.doc_sources else "-",
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Update only the Congresses rows in A + F of an existing workbook.")
    p.add_argument("--auditor", default="Naeem_7")
    p.add_argument("--engagement", default="20270117")
    p.add_argument("--tpi-path", dest="tpi_path",
                   default="/Volumes/nbod_gaagd/cpr_risk_analysis/tpi's_inventory_list/Naeem_7/TPI Inventory List_Naeem 7.xlsx")
    p.add_argument("--gl-path", dest="gl_path",
                   default="/Volumes/nbod_gaagd/cpr_risk_analysis/global_policies/HCC_GL_Account_Map-11142023.xlsx")
    p.add_argument("--out", default="outputs/phase3_outputs.xlsx", help="existing workbook to patch")
    args = p.parse_args()

    os.environ["AUDITOR_ID"] = args.auditor
    os.environ["AUDIT_ENGAGEMENT_NUMBERS"] = args.engagement
    os.environ["TPI_INVENTORY_FILE_PATH"] = args.tpi_path
    os.environ["HCC_GL_ACCOUNT_MAP_PATH"] = args.gl_path

    settings = get_settings()
    auditor_id = (args.auditor or settings.auditor_id).strip()
    llm = AzureOpenAILLMProvider(settings).get_client()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    if not out_path.exists():
        print(f"ERROR: workbook not found: {out_path} (run the full phase-3 first)")
        return 2

    try:
        _tpi_block, tpi_names = _prepare_reference_files(settings, auditor_id, llm=llm)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Congresses] reference-file prep failed | %s", exc)
        tpi_names = []

    agent = TransactionalDataAnalysisAgent(llm, settings, tpi_names=tpi_names)
    writer = OutputWriter(OutputRepository(), settings)
    repo = writer._repo
    display = SCOPE_ID_TO_DISPLAY_NAME[SCOPE_ID]

    # ── Re-run ONLY the Congresses scope ─────────────────────────────────────────────────────────
    logger.info("[Congresses] re-running single scope %s …", SCOPE_ID)
    guidance = agent._load_cpr_guidance(SCOPE_ID)
    data_summary, table_info = agent._pull_data_summary()
    findings = agent._analyze_scope(SCOPE_ID, guidance, data_summary, table_info)
    summary = agent._scope_summary_for(SCOPE_ID, guidance, table_info)
    logger.info("[Congresses] findings=%d | F txn_count=%d | F total_usd=%.2f",
                len(findings), summary.txn_count, summary.total_usd)

    repo._ensure_workbook(out_path)
    ws_a = repo._ws_a

    # ── Patch Output A: overwrite the existing Congresses row(s) in place ────────────────────────
    existing = [r for r in range(2, ws_a.max_row + 1)
                if str(ws_a.cell(r, _SCOPE_AREA_COL_A).value or "").strip() == display]
    snos = [ws_a.cell(r, 1).value for r in existing]
    new_rows = [writer._risk_output_to_row(f, sno=(snos[i] if i < len(snos) else (ws_a.max_row + i)))
                for i, f in enumerate(findings)]
    try:
        writer._attach_local_rows(new_rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Congresses] local-row attach skipped | %s", exc)

    if not new_rows:
        print("Output A — analysis returned NO findings; leaving existing Congresses row(s) untouched")
    else:
        for i, new_row in enumerate(new_rows):
            target = existing[i] if i < len(existing) else (ws_a.max_row + 1)
            repo._write_data_row(ws_a, new_row, target)
        # If the old sheet had MORE congress rows than the new result, blank the leftovers so they are
        # not mistaken for live rows (rare — a scope normally yields one Phase-3 row).
        for leftover in existing[len(new_rows):]:
            for c in range(1, ws_a.max_column + 1):
                ws_a.cell(leftover, c).value = None
        print(f"Output A — patched {min(len(new_rows), len(existing))} existing row(s), "
              f"appended {max(0, len(new_rows) - len(existing))} (scope had {len(existing)} before)")

    # ── Patch Output F: overwrite the Congresses row in place ────────────────────────────────────
    f_row = _output_f_row(display, summary)
    if SHEET_F_NAME in repo._wb.sheetnames:
        ws_f = repo._wb[SHEET_F_NAME]
        target_f = next((r for r in range(1, ws_f.max_row + 1)
                         if str(ws_f.cell(r, 1).value or "").strip() == display), None)
        if target_f:
            for c, val in enumerate(f_row, start=1):
                ws_f.cell(target_f, c).value = val
            print(f"Output F — patched Congresses row {target_f}: {f_row[1]} txns / {f_row[4]}")
        else:
            print("Output F — WARNING: Congresses row not found; sheet left unchanged")
    else:
        print(f"Output F — WARNING: sheet '{SHEET_F_NAME}' not present; skipped")

    repo._atomic_save(out_path)
    print(f"\nUpdated Congresses in A + F -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
