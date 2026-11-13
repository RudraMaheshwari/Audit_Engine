"""Regenerate ONLY Output F (Scope Transaction Summary) — standalone, no full risk-analysis run.

Builds the Transactional Data Analysis agent the same way the runner does (no ingestion — the
transactional dashboards + the auditor's supporting-doc volume must already exist), produces the
Output F rows, prints them for inspection, and writes them to an .xlsx. Use it to debug why Output F
is blank and to verify the table-side / doc-side counts and amounts in isolation.

Run (venv active, .env set with AUDITOR_ID / engagement / scope period / endpoint as for a real run):
    python scripts/generate_output_f.py
    python scripts/generate_output_f.py --auditor Naeem_7 --out outputs/output_f_naeem7.xlsx
"""

from __future__ import annotations

import argparse
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
from src.pipeline.output_f_stage import build_output_f_rows, build_output_g_rows  # noqa: E402
from src.repositories.output_repo import OutputRepository  # noqa: E402
from src.services.tpi_inventory import load_tpi_inventory_names  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger("generate_output_f")

_COLS = ["Scope Area", "Table Txns", "Doc Txns", "Total Txns",
         "Table Amount", "Doc Amount", "Total Amount", "Table Sources", "Doc Sources"]


def main() -> int:
    p = argparse.ArgumentParser(description="Regenerate only Output F (Scope Transaction Summary).")
    p.add_argument("--auditor", default=None, help="auditor_id (default: Settings.auditor_id)")
    p.add_argument("--out", default="outputs/output_f_only.xlsx", help="output .xlsx path")
    args = p.parse_args()

    settings = get_settings()
    auditor_id = (args.auditor or settings.auditor_id).strip()
    llm = AzureOpenAILLMProvider(settings).get_client()

    logger.info("[OutputF] auditor=%s | endpoint=%s | scope_period=%s",
                auditor_id, settings.databricks_vector_search_endpoint,
                settings.audit_scope_context or "(none)")

    # TPI inventory names feed the Due-Diligence doc side; best-effort (empty is fine).
    try:
        tpi_names = load_tpi_inventory_names(settings, llm=llm)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OutputF] TPI inventory load skipped | %s", exc)
        tpi_names = []

    agent = TransactionalDataAnalysisAgent(llm, settings, tpi_names=tpi_names)

    logger.info("[OutputF] Building scope transaction summary…")
    rows = build_output_f_rows(agent)

    # Print every row so the table-side / doc-side counts + amounts are visible for debugging.
    print("\n" + "#" * 90 + "\nOUTPUT F — Scope Transaction Summary (USD only)\n" + "#" * 90)
    nonblank = 0
    for row in rows:
        cells = [str(c) for c in row]
        if any(c not in ("-", "") for c in cells[1:7]):   # any count/amount populated
            nonblank += 1
        print(" | ".join(f"{h}: {v}" for h, v in zip(_COLS, cells)))
    print("#" * 90)
    print(f"Scopes: {len(rows)} | with data: {nonblank} | blank: {len(rows) - nonblank}")

    # Output G — HCC activity types not mapped to any scope area (count + USD).
    g_rows = build_output_g_rows(agent)
    print("\n" + "#" * 90 + "\nOUTPUT G — Other (unmapped) HCC Activity Types\n" + "#" * 90)
    for gr in g_rows:
        print(" | ".join(str(c) for c in gr))
    print(f"Unmapped HCC activity types: {max(0, len(g_rows) - 1)}")

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = OutputWriter(OutputRepository(), settings)
    writer.write_scope_txn_summary(rows, out_path)
    writer.write_other_hcc_types(g_rows, out_path)
    print(f"\nWrote Output F + Output G sheets -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
