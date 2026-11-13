"""Output F (Scope Transaction Summary) — its own stage, separated from the main runner.

Output F is the per-scope transaction summary sheet: for every canonical scope area, the transaction
COUNT and USD total, split by origin (dashboard "table side" vs supporting-document "doc side") plus a
combined total, with the source labels. Scopes with no transactions render '-'. Amounts are reported in
USD only (the pre-converted USD columns already present in the dashboards — no FX conversion).

Kept as a standalone stage so Output F can be built/written (and debugged/regenerated) on its own,
independent of a full risk-analysis run — see scripts/generate_output_f.py.
"""

from __future__ import annotations

from src.config.constants import SCOPE_ID_TO_DISPLAY_NAME
from src.utils.logger import get_logger

logger = get_logger(__name__)


def build_output_f_rows(transactional_agent) -> list[tuple]:
    """Build the Output F rows (one 9-tuple per canonical scope), amounts in USD only.

    Pure build step (no file I/O) so it can be unit-tested / inspected on its own. Each row is:
      (scope_display, table_txns, doc_txns, total_txns, table_amt, doc_amt, total_amt,
       table_sources, doc_sources)
    """
    summaries = transactional_agent.build_scope_transaction_summary()
    by_scope = {s.scope_id: s for s in summaries}

    def _amount_cell(usd_by_date: dict, usd_total: float, present: bool) -> str:
        if not present:
            return "-"
        # A count with no monetary data (e.g. a name-list supporting doc that has entries but no money
        # column) — show "-" for the amount rather than a misleading "USD 0.00".
        if not usd_by_date and not usd_total:
            return "-"
        return f"USD {usd_total:,.2f}"

    rows: list[tuple] = []
    for scope_id, display in SCOPE_ID_TO_DISPLAY_NAME.items():
        summary = by_scope.get(scope_id)
        if summary is None:
            rows.append((display, "-", "-", "-", "-", "-", "-", "-", "-"))
            continue

        table_present = summary.txn_count > 0 or bool(summary.usd_by_date)
        doc_present = summary.doc_txn_count > 0 or bool(summary.doc_usd_by_date)

        # Transaction counts split by origin + combined total.
        table_txns = summary.txn_count if table_present else "-"
        doc_txns = summary.doc_txn_count if doc_present else "-"
        total_txns = (summary.txn_count + summary.doc_txn_count) if (table_present or doc_present) else "-"

        table_amt = _amount_cell(summary.usd_by_date, summary.total_usd, table_present)
        doc_amt = _amount_cell(summary.doc_usd_by_date, summary.doc_total_usd, doc_present)

        # Scopes that carry two tagged values in one amount cell (e.g. Pricing/Discounts:
        # "Sales: USD x; Discount: USD y") override the plain USD figure for the table + total cells.
        override = getattr(summary, "amount_display_override", None)
        if override and table_present:
            table_amt = override

        # Total amount across both origins.
        if override and table_present and not doc_present:
            total_amt = override
        elif table_present or doc_present:
            combined_by_date: dict = {}
            for d, u in list(summary.usd_by_date.items()) + list(summary.doc_usd_by_date.items()):
                combined_by_date[d] = combined_by_date.get(d, 0.0) + u
            total_amt = _amount_cell(
                combined_by_date, summary.total_usd + summary.doc_total_usd, True
            )
        else:
            total_amt = "-"

        rows.append((
            display,
            table_txns,
            doc_txns,
            total_txns,
            table_amt,
            doc_amt,
            total_amt,
            "; ".join(summary.table_sources) if summary.table_sources else "-",
            "; ".join(summary.doc_sources) if summary.doc_sources else "-",
        ))

    return rows


def build_and_write_output_f(transactional_agent, output_writer, output_path) -> list[tuple]:
    """Build the Output F rows and write the sheet. Returns the rows (for logging / reuse)."""
    rows = build_output_f_rows(transactional_agent)
    output_writer.write_scope_txn_summary(rows, output_path)
    logger.info("[OutputF] Scope txn summary written | scopes=%d (USD only)", len(rows))
    return rows


def build_output_g_rows(transactional_agent) -> list[tuple]:
    """Output G rows — HCC activity types NOT mapped to any scope area, each with its transaction count
    + total USD, plus a grand-total row. Each row: (activity_type, txn_count, 'USD x.xx')."""
    items = transactional_agent.build_other_hcc_activities()   # [(type, count, usd), …]
    rows: list[tuple] = [(name, cnt, f"USD {amt:,.2f}") for name, cnt, amt in items]
    if rows:
        total_cnt = sum(cnt for _n, cnt, _a in items)
        total_amt = sum(amt for _n, _c, amt in items)
        rows.append(("TOTAL", total_cnt, f"USD {total_amt:,.2f}"))
    return rows


def build_and_write_output_g(transactional_agent, output_writer, output_path) -> list[tuple]:
    """Build the Output G rows (unmapped HCC activity types) and write the sheet."""
    rows = build_output_g_rows(transactional_agent)
    output_writer.write_other_hcc_types(rows, output_path)
    logger.info("[OutputG] Other HCC types written | rows=%d", len(rows))
    return rows
