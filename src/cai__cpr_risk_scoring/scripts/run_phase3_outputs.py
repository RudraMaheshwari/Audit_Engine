"""Run Phase 3 (transactional analysis) and emit ONLY the sheets it feeds — Output A (the Phase-3
findings), Output F (scope transaction summary) and Output G (other/unmapped HCC types) — into one
workbook, structured exactly like the final version. No per-question pipeline, no Output B/C/D/E.

Builds the SAME transactional analysis agent + output writer the real run uses (no ingestion — the
dashboards + TPI inventory must already exist). Useful to validate Phase 3 → A / F / G end-to-end
without a full risk-analysis run.

Run (venv active, .env set with AUDITOR_ID / engagement / scope period / endpoint as for a real run):
    python scripts/run_phase3_outputs.py
    python scripts/run_phase3_outputs.py --auditor Naeem_7 --out outputs/phase3_naeem7.xlsx
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
from src.config.settings import get_settings  # noqa: E402
from src.infrastructure.llm.azure_openai_provider import AzureOpenAILLMProvider  # noqa: E402
from src.output.output_writer import OutputWriter  # noqa: E402
from src.pipeline.output_f_stage import build_and_write_output_f, build_and_write_output_g  # noqa: E402
from src.pipeline.runner import _prepare_reference_files  # noqa: E402
from src.repositories.output_repo import OutputRepository  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("run_phase3_outputs")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Run Phase 3 and write only Output A (findings) + Output F + Output G.")
    p.add_argument("--auditor", default="Naeem_7", help="auditor_id (default: Naeem_7)")
    p.add_argument("--engagement", default="20270117",
                   help="audit engagement number(s), comma-separated (default: 20270117)")
    p.add_argument(
        "--tpi-path", dest="tpi_path",
        default="/Volumes/nbod_gaagd/cpr_risk_analysis/tpi's_inventory_list/Naeem_7/TPI Inventory List_Naeem 7.xlsx",
        help="TPI inventory file (Volume path) — downloaded to local cache, like the pipeline",
    )
    p.add_argument(
        "--gl-path", dest="gl_path",
        default="/Volumes/nbod_gaagd/cpr_risk_analysis/global_policies/HCC_GL_Account_Map-11142023.xlsx",
        help="HCC GL account map (Volume path)",
    )
    p.add_argument("--out", default="outputs/phase3_outputs.xlsx", help="output .xlsx path")
    args = p.parse_args()

    # Apply auditor + engagement via env BEFORE settings load, so the agent's engagement filter
    # (ENGAGEMENT_NUMBER IN (...)) and auditor-scoped lookups use them. Overrides .env for this run.
    os.environ["AUDITOR_ID"] = args.auditor
    os.environ["AUDIT_ENGAGEMENT_NUMBERS"] = args.engagement
    os.environ["TPI_INVENTORY_FILE_PATH"] = args.tpi_path
    os.environ["HCC_GL_ACCOUNT_MAP_PATH"] = args.gl_path

    settings = get_settings()
    auditor_id = (args.auditor or settings.auditor_id).strip()
    llm = AzureOpenAILLMProvider(settings).get_client()
    logger.info("[Phase3] engagement filter=%s", settings.engagement_filter_clause or "(none)")

    logger.info("[Phase3] auditor=%s | endpoint=%s | scope_period=%s",
                auditor_id, settings.databricks_vector_search_endpoint,
                settings.audit_scope_context or "(none)")

    # Download the TPI inventory + HCC GL map from the Volumes to local cache and repoint settings —
    # EXACTLY as the normal pipeline does — then hand the loaded TPI names to the agent.
    try:
        _tpi_block, tpi_names = _prepare_reference_files(settings, auditor_id, llm=llm)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Phase3] reference-file prep failed | %s", exc)
        tpi_names = []
    logger.info("[Phase3] TPI names=%d | sheet=%s | gl_map=%s",
                len(tpi_names), settings.tpi_inventory_sheet or "(none)", settings.hcc_gl_account_map_path)

    agent = TransactionalDataAnalysisAgent(llm, settings, tpi_names=tpi_names)
    writer = OutputWriter(OutputRepository(), settings)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()   # clean workbook each run

    # ── Phase 3 — transactional analysis (one RiskOutput per scope) ──────────────────────────────
    logger.info("[Phase3] Running transactional analysis (run_all)…")
    findings = agent.run_all()
    logger.info("[Phase3] Phase 3 complete | findings=%d", len(findings))

    # ── Output A — the Phase-3 transactional findings, same structure as the final workbook ───────
    rows = [writer._risk_output_to_row(f, sno=i + 1) for i, f in enumerate(findings)]
    try:
        writer._attach_local_rows(rows)   # bilingual local-language renderings (best-effort)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Phase3] local-row attach skipped | %s", exc)
    writer._repo.write_excel(rows, scope_summary=None, output_path=out_path)
    print(f"\nOutput A — wrote {len(rows)} transactional finding row(s)")

    # ── Output F — per-scope transaction summary (USD) ────────────────────────────────────────────
    f_rows = build_and_write_output_f(agent, writer, out_path)
    print(f"Output F — wrote {len(f_rows)} scope row(s)")

    # ── Output G — other/unmapped HCC activity types ──────────────────────────────────────────────
    g_rows = build_and_write_output_g(agent, writer, out_path)
    print(f"Output G — wrote {max(0, len(g_rows) - 1)} unmapped HCC type(s) (+ total row)")

    print(f"\nWrote Output A + F + G -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
