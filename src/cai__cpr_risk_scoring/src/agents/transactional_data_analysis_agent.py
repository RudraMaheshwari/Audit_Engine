from __future__ import annotations

import json
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import openpyxl
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from src.config.constants import (
    AGENT_TRANSACTIONAL_ANALYSIS,
    DASHBOARD_WORKFLOW_NAMES,
    RISK_CATEGORY_NONE,
    RISK_CATEGORY_NOT_CATEGORIZED,
    SCOPE_ID_TO_DISPLAY_NAME,
)
from src.config.databricks_auth import bearer_token, build_engine
from src.scoring import score_risk
from src.config.settings import Settings
from src.ingestion.extractors.cpr_scope_extractor import _resolve_scope_id
from src.ingestion.volume_downloader import DatabricksVolumeDownloader, download_volume_file
from src.prompts.transactional_analysis_prompt import (
    ANALYSIS_SYSTEM_PROMPT,
    ANALYSIS_USER_TEMPLATE,
    CALC_SQL_REPAIR_SYSTEM_PROMPT,
    CALC_SQL_REPAIR_TEMPLATE,
    CALC_SQL_SYSTEM_PROMPT,
    CALC_SQL_USER_TEMPLATE,
    SCOPE_SUMMARY_SQL_SYSTEM_PROMPT,
    SCOPE_SUMMARY_SQL_USER_TEMPLATE,
)
from src.schemas.state import RiskOutput, RiskType, SourceDetail
from src.services.gl_account_map import format_gl_account_map_block, load_gl_account_map
from src.services.tpi_inventory import format_tpi_inventory_block, load_tpi_inventory_names
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Column indices in CPR Excel (1-based)
_CPR_COL_SCOPE_DESC    = 3   # C
_CPR_COL_RED_FLAGS     = 4   # D
_CPR_COL_DATA_CALCS    = 9   # I
_CPR_COL_SAMPLING_RAT  = 10  # J
_CPR_COL_SAMPLE_SIZE   = 11  # K
_CPR_HEADER_ROWS       = 3   # first 3 rows are headers; data starts row 4

_SAMPLE_SEPARATOR = "─" * 60
# Divider placed between successive calculation blocks in Calculation Evidence.
_CALC_BLOCK_SEPARATOR = "\n" + "─" * 60 + "\n"

# Sample sizing: pull exactly the Expected Sample Size from CPR col K rows per identified risk.
_DEFAULT_SAMPLE_SIZE = 7   # fallback when col K has no usable number (e.g. "10%", "All", blank)

# How many scope areas to analyse concurrently in run_all() (each scope = 1 LLM call + SQL).
_SCOPE_ANALYSIS_WORKERS = 6

# Column-name hints for categorical columns whose REAL distinct values are surfaced to the calc-SQL
# builder, so a scope's activity filter matches actual values (e.g. CATEGORY='Fee for Service') rather
# than a guessed term. Keeps FFS/Congress-style scopes from silently matching nothing.
_CATEGORICAL_NAME_HINTS = ("category", "type", "classification", "status", "government_vendors")
_MAX_CATEGORICAL_COLS = 6
_MAX_CATEGORICAL_VALUES = 40

# Columns that are identifiers / codes / period keys — NEVER meaningful to SUM in the evidence summary
# (summing SUPPLIER_ID or COMPANY_CODE is noise). Matched on word boundaries so 'paid'/'invoice' are safe.
_NON_MEASURE_COL_RE = re.compile(
    r"(^|_)(id|code|number|no|key|guid|uuid|zip|year|month|quarter|date)(_|$)", re.IGNORECASE
)

# Deterministic scope → column filter resolver map.
# Each scope maps to an ordered list of (table_key, column_name, keyword_hints).
# At runtime, each entry is tried in order: distinct values of `column_name` in `table_key` are fetched,
# then matched (case-insensitive substring) against `keyword_hints`. The FIRST entry that produces
# ≥1 match wins and is injected as a mandatory `column IN (...)` predicate. Keywords are exact
# lowercase fragments of the REAL values observed in the live data — no guessing.
_SCOPE_CATEGORY_RESOLVER_MAP: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {
    "CONGRESSES__JJ_ORGANIZED_EVENTS": [
        ("hcc", "AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("congresses",)),
        ("hcc", "CATEGORY",                               ("event invitee",)),
    ],
    "FEE_FOR_SERVICE__DOCTOR_CONSULTING": [
        ("hcc", "AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("fee for service",)),
        ("hcc", "CATEGORY",                               ("fee for service",)),
    ],
    "EDUCATIONAL_GRANTS": [
        ("hcc", "AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("educational grant", "research grant")),
        ("hcc", "CATEGORY",                               ("grant",)),
    ],
    "SPONSORSHIPS": [
        ("hcc", "AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("promotional sponsorship",)),
        ("hcc", "CATEGORY",                               ("event invitee",)),
    ],
    "VALUE_ADD__PATIENT_SUPPORT_PROG": [
        ("hcc", "AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("patient support", "psp")),
        ("hcc", "CATEGORY",                               ("patient support", "psp", "value add")),
    ],
    "PRODUCT_TRAINING": [
        ("hcc", "AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("product training", "training")),
        ("hcc", "CATEGORY",                               ("product training", "training")),
    ],
    "DONATIONS": [
        ("hcc", "AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("donation",)),
        ("hcc", "CATEGORY",                               ("donation",)),
    ],
    # Pricing / Revenue dashboard scopes — these have transactional data even when HCC is empty, so
    # they must resolve to the pricing/revenue tables (not HCC). Tried in order; first match wins.
    "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS": [
        ("revenue", "TRANSACTION_TYPE", ("sale",)),
        ("pricing", "TRANSACTION_TYPE", ("sale",)),
    ],
    "PRICE_OVERRIDES": [
        ("pricing", "TRUE_PRICE_OVERRIDE", ("y",)),
        ("revenue", "TRUE_PRICE_OVERRIDE", ("y",)),
    ],
    "REBATES_PAID_TO_DISTRIBUTORS": [
        ("revenue", "ORDER_REASON_DESCRIPTION", ("rebate",)),
        ("pricing", "ORDER_REASON_DESCRIPTION", ("rebate",)),
    ],
    "FREE_GOODS": [
        ("otc", "TRANSACTION_TYPE",      ("free good",)),
    ],
    "PRODUCT_SAMPLES": [
        ("otc", "SALE_TYPE_DESCRIPTION", ("sample",)),
    ],
    "CREDIT_NOTES": [
        ("otc", "TRANSACTION_TYPE",      ("credit memo",)),
    ],
    "EXPENSE_REPORTS": [
        ("travel_expense", "RPT_ENT_EXP_TYPE_NM", ("hcp", "meal", "symposium", "sponsorship", "registration")),
    ],
}


# Legal-entity form tokens dropped (wherever they appear — prefix OR suffix) when normalising a
# payee/TPI name for matching, so "ZEST HEALTHCARE COMMUNICATIONS PTY LTD" and "Zest Healthcare
# Communications" match, and the Indonesian "PT KALBE FARMA" matches "KALBE FARMA TBK". Kept broad and
# international (these lists are country-specific — here IDR/Indonesian) so no dataset silently misses.
_LEGAL_SUFFIX_TOKENS = {
    # Anglo / common
    "PTY", "LTD", "LIMITED", "INC", "INCORPORATED", "LLC", "PLC", "CO", "CORP", "CORPORATION",
    "COMPANY", "COMPANIES", "LLP", "LP",
    # European
    "GMBH", "MBH", "BV", "NV", "SA", "AG", "AB", "AS", "OY", "SRL", "SPA", "SAS", "SARL", "SASU",
    "SL", "KG", "OHG", "KFT", "ZRT", "EOOD", "OOD", "AD", "DOO", "DOOEL", "OOO", "SP", "ZOO",
    "APS", "APB", "KS", "SC", "SE",
    # Asia-Pacific (Indonesia/Malaysia/Singapore/Japan/Korea/etc.)
    "PT", "TBK", "PERSERO", "PERSEROAN", "TERBATAS", "TERBUKA", "CV", "UD", "FA",
    "PTE", "SDN", "BHD", "BERHAD", "KK", "GK", "YK", "LLK",
    # Middle East free-zone forms
    "FZE", "FZCO", "FZ", "DMCC",
}

# Connector / stop tokens dropped as non-distinctive noise ("Johnson AND Johnson", "Bank OF ...",
# Indonesian "DAN"). Never carry entity identity, so they only dilute token-set matching.
_CONNECTOR_TOKENS = {"AND", "THE", "OF", "DE", "DI", "DU", "DA", "DAN", "EN", "ET", "UND"}

# Combined drop-set applied during normalisation.
_DROP_TOKENS = _LEGAL_SUFFIX_TOKENS | _CONNECTOR_TOKENS


def _normalize_entity(name) -> str:
    """Normalise an entity/payee name for suffix/punctuation/case-insensitive matching.

    Fold accents to ASCII (NESTLÉ -> NESTLE) so Latin variants align; drop punctuation and legal-form /
    connector tokens (wherever they sit). KEEPS region/qualifier tokens such as 'NZ' (from '(NZ)') so
    genuinely different legal entities — e.g. IQVIA (NZ) vs IQVIA Australia — are NOT merged. Non-Latin
    scripts (CJK/Hangul) fold away here and match via their romanised/English variant added upstream."""
    import re as _re
    import unicodedata as _ud
    raw = str(name) if name is not None else ""
    # NFKD-fold diacritics to bare ASCII letters so "É"/"Ü"/"Ñ" survive as "E"/"U"/"N" (not stripped).
    folded = "".join(c for c in _ud.normalize("NFKD", raw) if not _ud.combining(c))
    s = folded.upper()
    s = _re.sub(r"[^A-Z0-9 ]+", " ", s)    # drop punctuation (parentheses -> spaces; inner tokens kept)
    tokens = [t for t in s.split() if t and t not in _DROP_TOKENS]
    return " ".join(tokens)


# Supporting-document source label per scope (col E on the summary sheet). Names the doc that scope's
# data is expected from, even when the amount cannot yet be computed from it (shown as a reference).
_SCOPE_DOC_SOURCES: dict[str, list[str]] = {
    "DUE_DILIGENCE":                   ["TPI Inventory List"],
    "TENDERS":                         ["IDR Tender Report (Tab 4, Q68)"],
    "VALUE_ADD__PATIENT_SUPPORT_PROG": ["PSP List (IDR)"],
    "PAYMENTS_IN_AP_TO_DISTRIBUTORS":  ["Distributor List (IDR)"],
}

# Name-list scopes: the transaction count/amount comes from filtering the dashboards to an ENTITY LIST.
# scope_id -> (filename keywords to auto-discover the list in supporting docs, dashboard tables to
# filter, col-E source label). DUE_DILIGENCE uses the already-loaded structured TPI inventory, so it
# has no discovery keywords; the others auto-discover their list file from the auditor's supporting docs.
# Both the Disbursement and Payment AP dashboards are included (per reviewer: "Payments & Disbursements
# data"). They can report the same AP population, so Total Transactions sums across both by design.
_NAME_LIST_SCOPES: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "DUE_DILIGENCE":                   ((), ("disbursement", "payment", "revenue"), "TPI Inventory List"),
    "PAYMENTS_IN_AP_TO_DISTRIBUTORS":  (("distributor",), ("disbursement", "payment"), "Distributor List (IDR)"),
    "VALUE_ADD__PATIENT_SUPPORT_PROG": (("psp", "patient support", "patient-support"), ("disbursement", "payment", "hcc"), "PSP List (IDR)"),
    "TENDERS":                         (("tender",), ("disbursement", "payment"), "IDR Tender Report (Tab 4, Q68)"),
}

# Payee/entity NAME-column hints per dashboard table (used to filter by an entity list).
_TABLE_NAME_HINTS: dict[str, tuple[str, ...]] = {
    "disbursement": ("supplier", "vendor"),
    "payment":      ("supplier", "vendor"),
    "revenue":      ("customer",),
    "hcc":          ("institution", "organization", "supplier", "customer"),
}

_SUPPORTING_DOC_SHEET_EXTS = {".xlsx", ".xls", ".csv"}


@dataclass
class ScopeTxnSummary:
    """Per-scope transaction totals for the summary sheet. `usd_by_date` maps an ISO date string
    (or '' when no date) to the summed USD spend, so a caller can convert per transaction date.

    Amounts are tracked by ORIGIN so the sheet can show them separately plus a total:
      - table side  (txn_count / total_usd / usd_by_date)      → from the dashboard tables
      - doc side    (doc_txn_count / doc_total_usd / doc_usd_by_date) → from supporting documents
    `table_sources` / `doc_sources` name where each side's figures came from (cols D / E)."""
    scope_id: str
    # Transactional-table (dashboard) side
    txn_count: int = 0
    total_usd: float = 0.0
    usd_by_date: dict[str, float] = field(default_factory=dict)
    table_sources: list[str] = field(default_factory=list)
    # Supporting-document side
    doc_txn_count: int = 0
    doc_total_usd: float = 0.0
    doc_usd_by_date: dict[str, float] = field(default_factory=dict)
    doc_sources: list[str] = field(default_factory=list)
    has_data: bool = False


def _read_col(ws, col_idx: int, start_row: int) -> str:
    parts: list[str] = []
    for row in ws.iter_rows(min_row=start_row, min_col=col_idx, max_col=col_idx, values_only=True):
        val = row[0]
        if val is not None:
            text_val = str(val).strip()
            if text_val:
                parts.append(text_val)
    return "\n".join(parts)


class TransactionalDataAnalysisAgent:
    """
    AI-driven transactional analysis agent guided by CPR Audit Guidance.

    Produces ONE consolidated row per scope area. For each scope:
      1. Loads CPR guidance (Scope Description, Red Flags, Data Calculations,
         Sampling Rationale, Expected Sample Size) from the CPR Excel file — and where the
         Data Calculations are missing, the AI falls back to its own FCPA intelligence
      2. Pulls aggregated data summaries from all 7 dashboard tables (once, shared)
      3. Uses LLM + guidance + FCPA intelligence to produce a single consolidated risk
         assessment for the scope
      4. When a risk is present, pulls exactly the Expected Sample Size of sample rows for handoff
    """

    def __init__(self, llm: BaseChatModel, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings
        self._engine = self._build_engine(settings)
        self._distinct_values_cache: dict[tuple[str, str], list[str]] = {}
        # TPI inventory (per-auditor sheet) for supplier/payee → in-scope-TPI matching.
        tpi_names = load_tpi_inventory_names(settings, llm=llm)
        self._tpi_names = [n for n in (tpi_names or []) if str(n).strip()]
        self._tpi_inventory_block = format_tpi_inventory_block(tpi_names, settings.tpi_inventory_sheet)
        # HCC GL account map for verifying GL codes against the approved activity mapping.
        gl_mapping = load_gl_account_map(settings)
        self._gl_account_map_block = format_gl_account_map_block(gl_mapping)
        logger.info(
            "[%s] Compiled  tables=%s | tpi_inventory_names=%d (sheet=%s) | gl_account_mappings=%d",
            AGENT_TRANSACTIONAL_ANALYSIS,
            list(settings.dashboard_tables.values()),
            len(tpi_names), settings.tpi_inventory_sheet, len(gl_mapping),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Audit population (engagement number) helpers
    # ──────────────────────────────────────────────────────────────────────────

    @cached_property
    def _engagement_filter(self) -> str:
        """The mandatory audit-population predicate injected into prompts, of the form
        'ENGAGEMENT_NUMBER IN (<engagement numbers from .env>)'."""
        clause = self._settings.engagement_filter_clause
        return clause or "ENGAGEMENT_NUMBER (scope to this audit's engagement number — none configured)"

    def _engagement_where(self) -> str:
        """SQL WHERE fragment scoping a direct data-summary query to this audit's engagement
        number(s), or '' if none configured."""
        clause = self._settings.engagement_filter_clause
        return f"WHERE {clause}" if clause else ""

    def _distinct_column_values(self, table_key: str, column: str) -> list[str]:
        """Return cached distinct non-null values of `column` from the table mapped to `table_key`."""
        cache_key = (table_key, column)
        if cache_key in self._distinct_values_cache:
            return self._distinct_values_cache[cache_key]
        table = self._settings.dashboard_tables.get(table_key)
        if not table:
            self._distinct_values_cache[cache_key] = []
            return []
        try:
            rows = self._run_sql(
                f"SELECT DISTINCT {column} AS v FROM {table} {self._engagement_where()}"
            )
            values = [str(r.get("v")).strip() for r in rows if r.get("v") not in (None, "")]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] Could not read distinct %s.%s | %s",
                AGENT_TRANSACTIONAL_ANALYSIS, table_key, column, exc,
            )
            values = []
        self._distinct_values_cache[cache_key] = values
        return values

    # ──────────────────────────────────────────────────────────────────────────
    # Tenders — IDR tender-list supporting document (best-effort, Tenders scope only)
    # ──────────────────────────────────────────────────────────────────────────

    @cached_property
    def _tender_list_block(self) -> str:
        """Retrieve the IDR tender-list supporting-doc excerpts so the Tenders scope can cross-check the
        Payments dashboard against the entities J&J is tendering with. Best-effort: returns '' on any
        failure (missing index, no doc, retrieval error) so the Tenders analysis still runs normally."""
        try:
            from src.infrastructure.embeddings import AzureOpenAIEmbeddingProvider
            from src.vector_store.index_manager import VectorIndexManager

            index_manager = VectorIndexManager(self._settings, AzureOpenAIEmbeddingProvider(self._settings))
            hits = index_manager.similarity_search(
                index_key="supporting",
                query="tender list bid tender value awarded government public tender participating entities",
                scope_id=None,
                top_k=6,
                filters={"auditor_folder": self._settings.auditor_id},
            )
            snippets = [(getattr(h, "content", "") or "").strip() for h in hits]
            snippets = [s[:800] for s in snippets if s]
            if not snippets:
                logger.info("[%s] No IDR tender-list content found in supporting docs — Tenders runs on data only",
                            AGENT_TRANSACTIONAL_ANALYSIS)
                return ""
            joined = "\n---\n".join(snippets)
            return (
                "=== IDR TENDER LIST (supporting documents — use ONLY for the Tenders scope) ===\n"
                "Excerpts from the audit's IDR tender-list supporting document. Use the entity names and "
                "tender values below to cross-check the Payments dashboard: look for payments made to these "
                "tendering entities, inspecting INVOICE_DESCRIPTION / PO_DESCRIPTION, the transaction value, "
                "and the payment date for possible bribery red flags. Do NOT invent entities not present here.\n"
                f"{joined}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Tender-list retrieval skipped | %s", AGENT_TRANSACTIONAL_ANALYSIS, exc)
            return ""

    def _tender_context_for(self, scope_id: str) -> str:
        """The tender-list block for the Tenders scope only; '' for every other scope."""
        resolved = _resolve_scope_id(scope_id.replace("_", " ")) or scope_id.strip().upper()
        return self._tender_list_block if resolved == "TENDERS" else ""

    @cached_property
    def _remediation_register_block(self) -> str:
        """Retrieve the audit's Management Action Plan (MAP) / GACS remediation & monitoring excerpts
        from the supporting-docs index so the judgment can see whether a finding it is about to raise
        was ALREADY identified and CLOSED/remediated (reviewer: a GACS-closed finding was reported as
        an open failure). Retrieved ONCE and reused for every scope. Best-effort: '' on any failure."""
        try:
            from src.infrastructure.embeddings import AzureOpenAIEmbeddingProvider
            from src.vector_store.index_manager import VectorIndexManager

            index_manager = VectorIndexManager(self._settings, AzureOpenAIEmbeddingProvider(self._settings))
            hits = index_manager.similarity_search(
                index_key="supporting",
                query=(
                    "management action plan MAP remediation status closed open finding observation "
                    "rating minor target completion date GACS final report monitoring review"
                ),
                scope_id=None,
                top_k=10,
                filters={"auditor_folder": self._settings.auditor_id},
            )
            snippets = [(getattr(h, "content", "") or "").strip()[:800] for h in hits]
            snippets = [s for s in snippets if s]
            if not snippets:
                logger.info("[%s] No MAP/GACS remediation content in supporting docs — closure "
                            "cross-check runs without a register", AGENT_TRANSACTIONAL_ANALYSIS)
                return ""
            joined = "\n---\n".join(snippets)
            return (
                "=== PRIOR-FINDING REMEDIATION REGISTER (MAP / GACS — supporting documents) ===\n"
                "Excerpts from the audit's Management Action Plan (MAP) / GACS remediation and monitoring "
                "documents. Each may show a previously-identified finding, its rating, its STATUS "
                "(Open / Closed / Remediated), and a remediation / target date. Use ONLY to check whether "
                "a finding you are about to raise was ALREADY identified and CLOSED/remediated (see the "
                "PRIOR-FINDING CLOSURE rule). Do NOT invent items not present here.\n"
                f"{joined}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] MAP/GACS remediation retrieval skipped | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, exc)
            return ""

    def _resolve_scope_category_filter(self, scope_id: str) -> str:
        """Return a mandatory `column IN ('v1','v2',…)` predicate for a scope whose population is
        distinguished by a categorical column. Tries each (table_key, column, keywords) entry in
        _SCOPE_CATEGORY_RESOLVER_MAP in order; the first entry whose keywords match ≥1 real distinct
        value wins. Returns '' when no entry matches (LLM falls back to table_info values)."""
        entries = _SCOPE_CATEGORY_RESOLVER_MAP.get(scope_id)
        if not entries:
            return ""
        for table_key, column, keywords in entries:
            real_values = self._distinct_column_values(table_key, column)
            matched = sorted({v for v in real_values if any(k in v.lower() for k in keywords)})
            if matched:
                quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in matched)
                return f"{column} IN ({quoted})"
        return ""

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def run_all(self) -> list[RiskOutput]:
        """Analyse EVERY scope area, each guided by its own CPR sheet, producing one row per scope.

        The CPR file has one sheet per scope area, each with its own Data Calculations,
        Sampling Rationale, and Expected Sample Size. This pulls the (scope-independent)
        transactional data summary once, then analyses the scopes CONCURRENTLY (each scope is an
        independent LLM call + sample query) while preserving the original scope order in output.
        """
        logger.info("[%s] ▶ START  mode=all-scopes", AGENT_TRANSACTIONAL_ANALYSIS)

        guidance_by_scope = self._load_all_cpr_guidance()
        if not guidance_by_scope:
            logger.warning(
                "[%s] No scope areas found in CPR file — nothing to analyse",
                AGENT_TRANSACTIONAL_ANALYSIS,
            )
            return []

        logger.info(
            "[%s] Scope areas to analyse: %d | %s",
            AGENT_TRANSACTIONAL_ANALYSIS, len(guidance_by_scope), list(guidance_by_scope),
        )

        # Data summary is the same 3 tables for every scope — pull it once and reuse.
        data_summary, table_info = self._pull_data_summary()

        # Pre-warm the MAP/GACS remediation register on the main thread: every scope worker reads this
        # cached_property, and cached_property is not thread-safe on first access — compute it once here
        # so the concurrent workers all share the single cached value (one retrieval, no race).
        _ = self._remediation_register_block

        scopes = list(guidance_by_scope.items())

        def _analyze_one(item: tuple[str, dict[str, str]]) -> list[RiskOutput]:
            scope_id, guidance = item
            try:
                return self._analyze_scope(scope_id, guidance, data_summary, table_info)
            except Exception as exc:
                logger.error(
                    "[%s] Scope analysis failed | scope=%s | error=%s",
                    AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc, exc_info=True,
                )
                return []

        workers = max(1, min(_SCOPE_ANALYSIS_WORKERS, len(scopes)))
        logger.info(
            "[%s] Analysing %d scope(s) with %d concurrent worker(s)",
            AGENT_TRANSACTIONAL_ANALYSIS, len(scopes), workers,
        )

        results: list[RiskOutput] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # pool.map preserves input order, so output scope order is deterministic.
            for rows in pool.map(_analyze_one, scopes):
                results.extend(rows)

        logger.info(
            "[%s] ✔ DONE  mode=all-scopes  scopes=%d  total_findings=%d",
            AGENT_TRANSACTIONAL_ANALYSIS, len(guidance_by_scope), len(results),
        )
        return results

    def run(self, scope_id: str) -> list[RiskOutput]:
        """Analyse a single scope area (kept for targeted/backward-compatible use)."""
        logger.info("[%s] ▶ START  scope=%s", AGENT_TRANSACTIONAL_ANALYSIS, scope_id)
        guidance = self._load_cpr_guidance(scope_id)
        data_summary, table_info = self._pull_data_summary()
        results = self._analyze_scope(scope_id, guidance, data_summary, table_info)
        logger.info(
            "[%s] ✔ DONE  scope=%s  total_findings=%d",
            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, len(results),
        )
        return results

    def build_scope_transaction_summary(self) -> list[ScopeTxnSummary]:
        """Per-scope transaction totals (count + USD spend grouped by date) for the summary sheet.

        One entry per CPR scope area. USD is summed per transaction date so the caller can convert to
        the audit's local currency at each date's FX rate. Scopes with no data get has_data=False.
        """
        guidance_by_scope = self._load_all_cpr_guidance()
        _data_summary, table_info = self._pull_data_summary()
        summaries: list[ScopeTxnSummary] = []
        for scope_id, guidance in guidance_by_scope.items():
            try:
                summaries.append(self._scope_summary_for(scope_id, guidance, table_info))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Scope summary failed | scope=%s | error=%s",
                               AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
                summaries.append(ScopeTxnSummary(scope_id=scope_id))
        # Attach the supporting-doc source label (col E) to each scope that has one, even when its
        # amount cannot yet be computed from that doc — so the sheet always shows where the data lives.
        for summary in summaries:
            doc_srcs = _SCOPE_DOC_SOURCES.get(summary.scope_id)
            if doc_srcs and not summary.doc_sources:
                summary.doc_sources = list(doc_srcs)
        logger.info("[%s] Built transaction summary for %d scope(s)",
                    AGENT_TRANSACTIONAL_ANALYSIS, len(summaries))
        return summaries

    def _name_list_summary(
        self, scope_id: str, names: list[str], table_keys: tuple[str, ...], doc_label: str
    ) -> ScopeTxnSummary:
        """Count/amount for a scope whose population is an ENTITY LIST: filter each dashboard in
        `table_keys` to `names` (token-matched) and sum. Table-side figures; `doc_label` is the col-E
        scoping reference. Returns an empty (dash) summary with the label when the list is empty."""
        if not names:
            logger.info("[%s] %s name-list summary skipped — no entities resolved",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id)
            return ScopeTxnSummary(scope_id=scope_id, doc_sources=[doc_label])

        total_count = 0
        usd_by_date: dict[str, float] = {}
        sources: list[str] = []
        for table_key in table_keys:
            hints = _TABLE_NAME_HINTS.get(table_key, ("supplier", "vendor", "customer", "name"))
            try:
                count, by_date, display = self._name_filtered_table_summary(table_key, hints, names)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] %s name-list table summary failed | table=%s | %s",
                               AGENT_TRANSACTIONAL_ANALYSIS, scope_id, table_key, exc)
                continue
            if count > 0:
                total_count += count
                for day_key, usd in by_date.items():
                    usd_by_date[day_key] = usd_by_date.get(day_key, 0.0) + usd
                if display and display not in sources:
                    sources.append(display)

        return ScopeTxnSummary(
            scope_id=scope_id,
            txn_count=total_count,
            total_usd=round(sum(usd_by_date.values()), 2),
            usd_by_date=usd_by_date,
            table_sources=sources,
            doc_sources=[doc_label],
            has_data=total_count > 0,
        )

    def _name_list_for_scope(self, scope_id: str, keywords: tuple[str, ...]) -> list[str]:
        """Resolve the entity list for a name-list scope: the loaded TPI inventory for DUE_DILIGENCE,
        else auto-discover + read the matching supporting-doc file from the auditor's volume."""
        if scope_id == "DUE_DILIGENCE":
            return self._tpi_names
        if not keywords:
            return []
        return self._supporting_doc_entity_names(scope_id, keywords)

    def _supporting_doc_entity_names(self, scope_id: str, keywords: tuple[str, ...]) -> list[str]:
        """Auto-discover the scope's list file in the auditor's supporting-docs volume (by filename
        keyword), download it, and extract the entity-name column (LLM-mapped). Best-effort → []."""
        try:
            parent = (self._settings.auditors_supporting_docs_volume_path or "").rstrip("/")
            auditor_vol = f"{parent}/{self._settings.auditor_id}"
            downloader = DatabricksVolumeDownloader(self._settings.databricks_host, bearer_token(self._settings))
            files = downloader.list_files(auditor_vol)
            candidates = [
                f for f in files
                if Path(f.get("name", "")).suffix.lower() in _SUPPORTING_DOC_SHEET_EXTS
                and any(k in f.get("name", "").lower() for k in keywords)
            ]
            if not candidates:
                logger.info("[%s] %s — no supporting-doc file matching %s in %s",
                            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, list(keywords), auditor_vol)
                return []
            chosen = candidates[0]
            cache_dir = Path(tempfile.gettempdir()) / "audit_phase3_refs"
            cache_dir.mkdir(parents=True, exist_ok=True)
            local_path = cache_dir / Path(chosen["name"]).name
            if not (local_path.exists() and local_path.stat().st_size > 0):
                if not downloader.download_single_file(chosen["path"], local_path):
                    logger.warning("[%s] %s — could not download %s",
                                   AGENT_TRANSACTIONAL_ANALYSIS, scope_id, chosen["path"])
                    return []
            headers, rows = self._load_sheet(local_path)
            if not headers or not rows:
                logger.info("[%s] %s — supporting-doc %s not tabular/empty",
                            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, chosen["name"])
                return []
            entity_col = self._map_entity_column(scope_id, headers, rows[:5])
            if entity_col is None or entity_col not in headers:
                logger.info("[%s] %s — could not identify entity column in %s (headers=%s)",
                            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, chosen["name"], headers)
                return []
            idx = headers.index(entity_col)
            names = []
            seen: set[str] = set()
            for r in rows:
                val = (str(r[idx]).strip() if idx < len(r) and r[idx] is not None else "")
                if val and val.lower() not in seen:
                    seen.add(val.lower())
                    names.append(val)
            logger.info("[%s] %s — supporting doc '%s' | entity_col=%s | entities=%d",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id, chosen["name"], entity_col, len(names))
            # Keep original + English variants so these names match a ledger in either language
            # (same discipline as the TPI inventory).
            try:
                from src.services.translation import TranslationService

                names = TranslationService(self._settings, llm=self._llm).augment_with_english(names)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] %s — name English-augmentation skipped | %s",
                               AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return names
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] %s — supporting-doc entity extraction failed | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return []

    @staticmethod
    def _load_sheet(local_path: Path) -> tuple[list[str], list[list]]:
        """Read the first sheet of an .xlsx/.xls/.csv into (headers, data_rows). The header is the
        first non-empty row; everything after is data. Returns ([], []) on failure."""
        suffix = local_path.suffix.lower()
        try:
            if suffix == ".csv":
                import csv
                with open(local_path, newline="", encoding="utf-8-sig", errors="ignore") as fh:
                    all_rows = [row for row in csv.reader(fh)]
            elif suffix == ".xls":
                # openpyxl cannot read the legacy .xls format — use pandas (xlrd engine).
                import pandas as pd
                df = pd.read_excel(local_path, sheet_name=0, header=None, dtype=object)
                all_rows = [
                    [None if pd.isna(v) else v for v in row]
                    for row in df.itertuples(index=False, name=None)
                ]
            else:
                wb = openpyxl.load_workbook(local_path, read_only=True, data_only=True)
                ws = wb[wb.sheetnames[0]]
                all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
                wb.close()
        except Exception as exc:  # noqa: BLE001
            hint = " (install xlrd or save as .xlsx)" if suffix == ".xls" else ""
            logger.warning("[%s] Could not read supporting-doc sheet %s%s | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, local_path.name, hint, exc)
            return [], []
        # First row with >= 2 non-empty cells is the header.
        header_idx = next(
            (i for i, r in enumerate(all_rows) if sum(1 for c in r if c not in (None, "")) >= 2),
            None,
        )
        if header_idx is None:
            return [], []
        headers = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
        data = [list(r) for r in all_rows[header_idx + 1:]]
        return headers, data

    def _map_entity_column(self, scope_id: str, headers: list[str], sample_rows: list[list]) -> str | None:
        """Ask the LLM which header holds the third-party/vendor/entity NAME. Heuristic fallback on
        failure. The LLM only maps a column — it never computes anything."""
        clean_headers = [h for h in headers if h]
        sample_block = "\n".join(
            " | ".join(str(c) if c is not None else "" for c in row) for row in sample_rows
        )
        user = (
            f"SCOPE: {scope_id}\nHEADERS: {clean_headers}\nSAMPLE ROWS:\n{sample_block}\n\n"
            'Which single column holds the third-party / vendor / distributor / entity NAME (not an id, '
            'code, date, or amount)? Return ONLY JSON: {"entity_column": "<exact header>"}'
        )
        try:
            resp = self._llm.invoke([
                SystemMessage(content="You map spreadsheet columns. Return only the requested JSON."),
                HumanMessage(content=user),
            ])
            raw = (resp.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            col = (json.loads(raw).get("entity_column") or "").strip()
            if col in headers:
                return col
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] %s — entity-column mapping failed, using heuristic | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
        # Heuristic fallback: first header that looks like an entity name column.
        for kw in ("name", "vendor", "supplier", "distributor", "entity", "party", "tpi", "organisation", "organization"):
            for h in headers:
                if h and kw in h.lower():
                    return h
        return None

    @staticmethod
    def _looks_like_money_header(header: str) -> bool:
        """True when a header names a money AMOUNT (not a percentage / date / id / count)."""
        hl = (header or "").lower()
        if not hl:
            return False
        return not any(x in hl for x in (
            "percent", "%", "rate", "date", "count", "number", "no.", "qty", "quantity",
            "id", "code", "year", "ratio",
        ))

    def _map_amount_column(self, scope_id: str, headers: list[str], sample_rows: list[list]) -> str | None:
        """Ask the LLM which header holds the transaction/payment AMOUNT (money value) in the doc,
        preferring a USD figure. Heuristic fallback on failure. Returns None when the doc has none."""
        clean_headers = [h for h in headers if h]
        sample_block = "\n".join(
            " | ".join(str(c) if c is not None else "" for c in row) for row in sample_rows
        )
        user = (
            f"SCOPE: {scope_id}\nHEADERS: {clean_headers}\nSAMPLE ROWS:\n{sample_block}\n\n"
            'Which single column holds the transaction / payment AMOUNT (a money value), preferring a '
            'USD amount if one is present? It must NOT be a date, id, code, count, quantity, or a '
            'percentage. If there is no money-amount column, return an empty string. '
            'Return ONLY JSON: {"amount_column": "<exact header or empty>"}'
        )
        try:
            resp = self._llm.invoke([
                SystemMessage(content="You map spreadsheet columns. Return only the requested JSON."),
                HumanMessage(content=user),
            ])
            raw = (resp.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            col = (json.loads(raw).get("amount_column") or "").strip()
            if col and col in headers:
                return col
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] %s — amount-column mapping failed, using heuristic | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
        # Heuristic fallback: prefer a USD money column, then a generic money column.
        for h in headers:
            if h and "usd" in h.lower() and self._looks_like_money_header(h):
                return h
        for kw in ("amount", "amt", "value", "total", "spend", "paid", "payment"):
            for h in headers:
                if h and kw in h.lower() and self._looks_like_money_header(h):
                    return h
        return None

    @staticmethod
    def _map_doc_date_column(headers: list[str]) -> str | None:
        """First header that looks like a transaction DATE (so doc amounts can be dated for FX)."""
        for h in headers:
            hl = (h or "").lower()
            if "date" in hl and not any(x in hl for x in ("count", "number", "no.", "update", "id")):
                return h
        return None

    def _supporting_doc_amount_summary(
        self, scope_id: str, keywords: tuple[str, ...]
    ) -> tuple[int, dict[str, float]]:
        """Best-effort DOC-SIDE figures for Output F: discover the scope's supporting-doc list file
        (same discovery as _supporting_doc_entity_names), read its AMOUNT column, and return
        (row_count, usd_by_date). Returns (0, {}) when the file or an amount column is absent — so the
        doc side simply stays blank (today's behaviour) and nothing regresses. Purely additive."""
        if not keywords:
            return 0, {}
        try:
            parent = (self._settings.auditors_supporting_docs_volume_path or "").rstrip("/")
            auditor_vol = f"{parent}/{self._settings.auditor_id}"
            downloader = DatabricksVolumeDownloader(self._settings.databricks_host, bearer_token(self._settings))
            files = downloader.list_files(auditor_vol)
            candidates = [
                f for f in files
                if Path(f.get("name", "")).suffix.lower() in _SUPPORTING_DOC_SHEET_EXTS
                and any(k in f.get("name", "").lower() for k in keywords)
            ]
            if not candidates:
                return 0, {}
            chosen = candidates[0]
            cache_dir = Path(tempfile.gettempdir()) / "audit_phase3_refs"
            cache_dir.mkdir(parents=True, exist_ok=True)
            local_path = cache_dir / Path(chosen["name"]).name
            if not (local_path.exists() and local_path.stat().st_size > 0):
                if not downloader.download_single_file(chosen["path"], local_path):
                    return 0, {}
            headers, rows = self._load_sheet(local_path)
            if not headers or not rows:
                return 0, {}
            amount_col = self._map_amount_column(scope_id, headers, rows[:5])
            if amount_col is None or amount_col not in headers:
                # Pure name/entity list (no money column) — these are entities, NOT transactions, so we
                # report nothing on the doc side. The scope's real count/amount must come from the
                # dashboards (table side); we no longer surface the misleading entity-list count here.
                logger.info("[%s] %s — no amount column in doc '%s' — doc side left blank (entity list, not transactions) (headers=%s)",
                            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, chosen["name"], headers)
                return 0, {}
            a_idx = headers.index(amount_col)
            date_col = self._map_doc_date_column(headers)
            d_idx = headers.index(date_col) if (date_col and date_col in headers) else None
            count = 0
            by_date: dict[str, float] = {}
            for r in rows:
                amt = self._coerce_number(r[a_idx]) if a_idx < len(r) else None
                if amt is None:
                    continue
                day_key = ""
                if d_idx is not None and d_idx < len(r) and r[d_idx] not in (None, ""):
                    day_key = str(r[d_idx])[:10]
                by_date[day_key] = by_date.get(day_key, 0.0) + float(amt)
                count += 1
            logger.info("[%s] %s — doc '%s' | amount_col=%s | rows=%d | total=%.2f",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id, chosen["name"], amount_col,
                        count, sum(by_date.values()))
            return count, by_date
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] %s — doc amount extraction failed | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return 0, {}

    def _name_filtered_table_summary(
        self, table_key: str, name_hints: tuple[str, ...], names: list[str]
    ) -> tuple[int, dict[str, float], str | None]:
        """Count + USD-by-date of this audit's rows on `table_key` whose name column matches `names`.
        Returns (count, usd_by_date, friendly_table_name) — (0, {}, None) when the table is missing,
        has no usable name/USD column, or matches nothing. Amounts summed only from a `*_USD` column."""
        table = self._settings.dashboard_tables.get(table_key)
        if not table or not names:
            return 0, {}, None
        schema = self._get_table_schema(table)          # [(name, dtype), ...]
        cols = [name for name, _ in schema]
        dtype_by_col = {name: (dtype or "").lower() for name, dtype in schema}

        def _pick(hints: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str | None:
            for hint in hints:
                for col in cols:
                    cl = col.lower()
                    if hint in cl and not any(x in cl for x in exclude):
                        return col
            return None

        # Prefer the actual payee *_NAME column (e.g. SUPPLIER_NAME / CUSTOMER_NAME), never an ID/code
        # column (SUPPLIER_IDENTIFIER, VENDOR_ID). Fall back to a hint-only match if no *_NAME exists.
        def _pick_name(hints: tuple[str, ...]) -> str | None:
            for hint in hints:
                for col in cols:
                    cl = col.lower()
                    if hint in cl and "name" in cl:
                        return col
            return _pick(hints, exclude=("id", "identifier", "code", "number", "group", "flag"))

        name_col = _pick_name(name_hints)
        usd_col = _pick(("usd",))
        # Date column: pick a genuinely DATE/TIMESTAMP-typed column — NOT a column merely named
        # '*_date' (e.g. COUNTDISTINCT_INVOICE_POSTING_DATE is a BIGINT count and must not be cast).
        date_col = next(
            (
                name for name in cols
                if ("date" in dtype_by_col.get(name, "") or "timestamp" in dtype_by_col.get(name, ""))
                and not any(x in name.lower() for x in ("count", "distinct", "num", "qty"))
            ),
            None,
        )
        if not name_col or not usd_col:
            logger.info("[%s] DD skip table %s — no usable %s column",
                        AGENT_TRANSACTIONAL_ANALYSIS, table_key,
                        "name" if not name_col else "USD amount")
            return 0, {}, None

        # Normalise each TPI name to a TOKEN SET (suffix/punctuation/case-insensitive). Exact-string
        # matching misses SAP payee variants ('PTY LTD', '(NZ)', 'trading as …'), so a payee matches a
        # TPI when one normalised token set is a subset of the other (guarded against trivial matches).
        def _token_set(value) -> frozenset[str]:
            return frozenset(_normalize_entity(value).split())

        def _significant(ts: frozenset[str]) -> bool:
            # avoid matching on a single short/common token (e.g. just "HEALTH")
            return len(ts) >= 2 or any(len(t) >= 5 for t in ts)

        tpi_sets = {ts for ts in (_token_set(n) for n in names) if ts}

        def _matches(payee_ts: frozenset[str]) -> bool:
            if not payee_ts:
                return False
            for ts in tpi_sets:
                small, big = (ts, payee_ts) if len(ts) <= len(payee_ts) else (payee_ts, ts)
                if not small or not _significant(small):
                    continue
                # Clean same-entity signal: every distinctive token of the shorter name is present.
                if small.issubset(big):
                    return True
                # Tolerate ONE differing qualifier token (e.g. "... INTERNATIONAL" vs "... INDONESIA")
                # but only for names of 3+ tokens and only when >=2 shared tokens overlap, one of them
                # distinctive (len>=4). Short names stay strict so unrelated payees are never merged.
                inter = small & big
                if len(small) >= 3 and len(inter) >= len(small) - 1 and len(inter) >= 2 \
                        and any(len(t) >= 4 for t in inter):
                    return True
            return False

        if not tpi_sets:
            return 0, {}, None

        # Pull per-payee, per-date aggregates for THIS audit, then match payees in Python.
        clause = self._settings.engagement_filter_clause
        where = f"WHERE {clause} " if clause else ""
        date_expr = f"try_cast(`{date_col}` AS DATE)" if date_col else "CAST(NULL AS DATE)"
        sql = (
            f"SELECT `{name_col}` AS payee, {date_expr} AS txn_date, COUNT(*) AS txn_count, "
            f"SUM(COALESCE(try_cast(`{usd_col}` AS DOUBLE), 0)) AS total_usd "
            f"FROM {table} {where}"
            f"GROUP BY `{name_col}`, {date_expr}"
        )
        rows = self._run_sql(sql)
        count = 0
        by_date: dict[str, float] = {}
        matched_payees: set[str] = set()
        all_payees: set[str] = set()
        for r in rows:
            payee = r.get("payee")
            if payee:
                all_payees.add(str(payee))
            if not _matches(_token_set(payee)):
                continue
            c = int(self._coerce_number(r.get("txn_count")) or 0)
            u = float(self._coerce_number(r.get("total_usd")) or 0.0)
            day = r.get("txn_date")
            key = "" if day in (None, "") else str(day)[:10]
            count += c
            by_date[key] = by_date.get(key, 0.0) + u
            if payee:
                matched_payees.add(str(payee))
        display = self._dashboard_source_name(table) if count > 0 else None
        logger.info(
            "[%s] DD table %s | name_col=%s | matched_txns=%d | matched_payees=%d (%s)",
            AGENT_TRANSACTIONAL_ANALYSIS, table_key, name_col, count, len(matched_payees),
            "; ".join(sorted(matched_payees)[:10]) or "none",
        )
        if count == 0:
            logger.info(
                "[%s] DD table %s | NO MATCH — sample payees=%s | sample TPIs=%s",
                AGENT_TRANSACTIONAL_ANALYSIS, table_key,
                sorted(all_payees)[:8] or "none",
                [" ".join(sorted(ts)) for ts in list(tpi_sets)[:8]],
            )
        return count, by_date, display

    def _scope_summary_for(self, scope_id: str, guidance: dict[str, str], table_info: str) -> ScopeTxnSummary:
        """Generate + run ONE count/USD-by-date summary query for a scope; aggregate into a summary."""
        # Name-list scopes (Due Diligence via TPI inventory; AP-to-distributors / PSP / Tenders via an
        # auto-discovered supporting-doc list) have no single-table category filter — their population is
        # an entity list filtered across the dashboards. Handle those on the deterministic name-list path.
        resolved = _resolve_scope_id(scope_id.replace("_", " ")) or scope_id.strip().upper()
        cfg = _NAME_LIST_SCOPES.get(resolved)
        if cfg:
            keywords, table_keys, doc_label = cfg
            names = self._name_list_for_scope(resolved, keywords)
            summary = self._name_list_summary(resolved, names, table_keys, doc_label)
            # DOC SIDE (additive): sum the supporting-doc's own amount column, if it has one, so the
            # sheet's doc count/amount are real figures — not just a source label. Leaves the doc side
            # blank (today's behaviour) when the doc has no amount column.
            doc_count, doc_by_date = self._supporting_doc_amount_summary(resolved, keywords)
            # Doc side is populated ONLY when the supporting doc has a genuine money/amount column
            # (real doc-side transactions). A pure name/entity list (TPI inventory, distributor list)
            # contributes NO count/amount — those are entities, not transactions. The scope's actual
            # figures come from the dashboards (table side above); the doc only provides the col-E label.
            if doc_count:
                summary.doc_txn_count = doc_count
                summary.doc_usd_by_date = doc_by_date
                summary.doc_total_usd = round(sum(doc_by_date.values()), 2)
                summary.has_data = summary.has_data or doc_count > 0
            return summary

        category_filter = self._resolve_scope_category_filter(scope_id)
        if not category_filter:
            return ScopeTxnSummary(scope_id=scope_id)
        user_msg = SCOPE_SUMMARY_SQL_USER_TEMPLATE.format(
            scope_area=scope_id,
            engagement_filter=self._engagement_filter,
            category_filter=category_filter or "(none — use the real CATEGORY values listed in the table info)",
            scope_description=guidance.get("scope_description") or "Not available for this scope area.",
            table_info=table_info or "Table schema not available.",
        )
        response = self._llm.invoke([
            SystemMessage(content=SCOPE_SUMMARY_SQL_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            sql = (json.loads(raw).get("sql") or "").strip()
        except Exception:
            sql = ""
        if not sql:
            return ScopeTxnSummary(scope_id=scope_id)
        if category_filter:
            sql = self._enforce_category_filter(sql, category_filter)

        rows = self._run_sql(sql)
        usd_by_date: dict[str, float] = {}
        txn_count = 0
        for r in rows:
            cnt = self._coerce_number(r.get("txn_count")) or 0
            usd = self._coerce_number(r.get("total_usd")) or 0.0
            day = r.get("txn_date")
            day_key = "" if day in (None, "") else str(day)[:10]
            txn_count += int(cnt)
            usd_by_date[day_key] = usd_by_date.get(day_key, 0.0) + float(usd)
        total_usd = round(sum(usd_by_date.values()), 2)
        table_ref = self._extract_table_from_sql(sql)
        table_sources = [self._dashboard_source_name(table_ref)] if (table_ref and txn_count > 0) else []
        return ScopeTxnSummary(
            scope_id=scope_id,
            txn_count=txn_count,
            total_usd=total_usd,
            usd_by_date=usd_by_date,
            table_sources=table_sources,
            has_data=txn_count > 0,
        )

    def resolve_local_currency(self) -> str:
        """Determine the audit's local currency AT RUNTIME from the data — the most common non-USD
        currency code for this engagement across the active dashboards. This is where the audit runs
        (e.g. AUD for an Australian audit), not a fixed env value. Falls back to the configured default
        only when no currency-code column/value can be found."""
        fallback = (self._settings.audit_local_currency or "AUD").upper()
        for table in self._settings.dashboard_tables.values():
            try:
                cols = self._get_table_columns(table)
            except Exception:
                continue
            currency_col = next(
                (
                    c for c in cols
                    if "currency" in c.lower()
                    and not any(x in c.lower() for x in ("amt", "amount", "usd", "value"))
                ),
                None,
            )
            if not currency_col:
                continue
            try:
                rows = self._run_sql(
                    f"SELECT UPPER(try_cast(`{currency_col}` AS STRING)) AS code, COUNT(*) AS cnt "
                    f"FROM {table} {self._engagement_where()} GROUP BY 1 ORDER BY cnt DESC LIMIT 10"
                )
            except Exception:
                continue
            for r in rows:
                code = (r.get("code") or "").strip()
                if code and code != "USD" and len(code) == 3:
                    logger.info(
                        "[%s] Resolved audit local currency from data = %s | col=%s | table=%s",
                        AGENT_TRANSACTIONAL_ANALYSIS, code, currency_col, table,
                    )
                    return code
        logger.info(
            "[%s] Local currency not found in data — using fallback %s",
            AGENT_TRANSACTIONAL_ANALYSIS, fallback,
        )
        return fallback

    def _analyze_scope(
        self,
        scope_id: str,
        guidance: dict[str, str],
        fallback_summary: str,
        table_info: str,
    ) -> list[RiskOutput]:
        """Produce exactly ONE consolidated risk row for this scope area.

        Flow: (1) turn the scope's Data Calculations into SQL and (2) execute it to get
        scope-specific computed data, then (3) judge risk on THOSE numbers. When no scope
        calculation can be produced, fall back to the shared generic data summary. Every scope
        yields one row; samples are pulled only for an actual risk, sized to the scope's
        Expected Sample Size (CPR col K) + buffer.
        """
        # Steps 1 & 2 — run this scope's own Data Calculations against the tables.
        calc_results, calc_sqls = self._run_scope_calculations(scope_id, guidance, table_info)
        data_for_judgment = calc_results or fallback_summary

        # Step 3 — judge risk on the scope-specific computed data.
        finding = self._analyze_with_ai(scope_id, guidance, data_for_judgment, table_info)
        if not finding:
            finding = {
                "risk_type": "No Risk Identified",
                "risk_title": "No transactional risk identified",
                "risk_description": "No material transactional risk was identified for this scope area.",
            }

        expected_size = self._parse_sample_size(guidance.get("expected_sample_size", ""))
        target_samples = expected_size  # pull exactly the CPR expected sample size (no buffer)

        risk_type_str = (finding.get("risk_type") or "").lower()
        is_actual_risk = "potential" in risk_type_str or "further" in risk_type_str
        logger.info(
            "[%s] Scope assessed | scope=%s | risk_type=%s | expected_size=%d | pulling=%d",
            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, finding.get("risk_type"),
            expected_size, target_samples,
        )

        samples_text = (
            self._pull_samples(finding.get("sample_query", ""), target_samples)
            if is_actual_risk else ""
        )
        return [self._to_risk_output(scope_id, finding, samples_text, calc_results, calc_sqls)]

    # ──────────────────────────────────────────────────────────────────────────
    # CPR Guidance Loading
    # ──────────────────────────────────────────────────────────────────────────

    def _load_all_cpr_guidance(self) -> dict[str, dict[str, str]]:
        """Load guidance for EVERY scope area in one pass over the CPR file.

        Returns {scope_id: guidance_dict} for all 48 scope areas. Scopes are NOT skipped when
        Data Calculations is empty — that column is passed through as-is and the AI is instructed
        to fall back to its own FCPA audit intelligence for those scopes.
        """
        path = self._local_reference_path(self._settings.scope_area_policy_file_path)
        if path is None or not path.exists():
            logger.warning("[%s] CPR file not available — cannot run all-scopes analysis",
                           AGENT_TRANSACTIONAL_ANALYSIS)
            return {}

        out: dict[str, dict[str, str]] = {}
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            start = _CPR_HEADER_ROWS + 1
            for sheet_name in wb.sheetnames:
                if sheet_name.strip().lower() == "index":
                    continue
                sid = _resolve_scope_id(sheet_name)
                if sid is None:
                    continue

                ws = wb[sheet_name]
                data_calcs = _read_col(ws, _CPR_COL_DATA_CALCS, start)
                if not data_calcs.strip():
                    logger.info("[%s] No Data Calculations in sheet — AI will use its own "
                                "intelligence | scope=%s | sheet=%s",
                                AGENT_TRANSACTIONAL_ANALYSIS, sid, sheet_name)

                out[sid] = {
                    "scope_description":    _read_col(ws, _CPR_COL_SCOPE_DESC,   start),
                    "red_flags":            _read_col(ws, _CPR_COL_RED_FLAGS,    start),
                    "data_calculations":    data_calcs,
                    "sampling_rationale":   _read_col(ws, _CPR_COL_SAMPLING_RAT, start),
                    "expected_sample_size": _read_col(ws, _CPR_COL_SAMPLE_SIZE,  start),
                }
            wb.close()
            logger.info("[%s] Loaded CPR guidance for %d scope area(s)",
                        AGENT_TRANSACTIONAL_ANALYSIS, len(out))
        except Exception as exc:
            logger.error("[%s] CPR bulk load error | error=%s", AGENT_TRANSACTIONAL_ANALYSIS, exc)
        return out

    def _load_cpr_guidance(self, scope_id: str) -> dict[str, str]:
        """Download CPR Excel and extract guidance columns for the given scope area."""
        path = self._local_reference_path(self._settings.scope_area_policy_file_path)
        if path is None or not path.exists():
            logger.warning("[%s] CPR file not available — proceeding with AI intelligence only | scope=%s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id)
            return {}

        # The incoming scope_id may be the config value ("tpi_due_diligence") rather than a
        # CPR scope key ("DUE_DILIGENCE"). Normalise it so it matches a sheet's resolved id.
        target_scope = _resolve_scope_id(scope_id.replace("_", " ")) or scope_id.strip().upper()
        if target_scope != scope_id:
            logger.info("[%s] Normalised scope for CPR match | %s → %s",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id, target_scope)

        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            for sheet_name in wb.sheetnames:
                if sheet_name.strip().lower() == "index":
                    continue
                if _resolve_scope_id(sheet_name) != target_scope:
                    continue

                ws = wb[sheet_name]
                start = _CPR_HEADER_ROWS + 1
                guidance = {
                    "scope_description":    _read_col(ws, _CPR_COL_SCOPE_DESC,   start),
                    "red_flags":            _read_col(ws, _CPR_COL_RED_FLAGS,    start),
                    "data_calculations":    _read_col(ws, _CPR_COL_DATA_CALCS,   start),
                    "sampling_rationale":   _read_col(ws, _CPR_COL_SAMPLING_RAT, start),
                    "expected_sample_size": _read_col(ws, _CPR_COL_SAMPLE_SIZE,  start),
                }
                wb.close()
                logger.info("[%s] CPR guidance loaded | scope=%s | sheet=%s",
                            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, sheet_name)
                return guidance

            wb.close()
            logger.warning("[%s] No CPR sheet matched scope=%s", AGENT_TRANSACTIONAL_ANALYSIS, scope_id)
        except Exception as exc:
            logger.error("[%s] CPR load error | scope=%s | error=%s",
                         AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
        return {}

    # ──────────────────────────────────────────────────────────────────────────
    # Transactional Data Summary
    # ──────────────────────────────────────────────────────────────────────────

    def pull_table_info(self) -> str:
        """Introspect the 7 dashboard tables (schema only, no aggregates) and return their column
        layouts as text — used to populate the raw-transactional-tables index for NL2SQL awareness."""
        parts: list[str] = []
        for _label, table in self._settings.dashboard_tables.items():
            try:
                schema = self._get_table_schema(table)
                parts.append(self._table_info_line(table, schema))
            except Exception as exc:
                parts.append(f"Table: {table}\nColumns: (schema unavailable: {exc})")
        return "\n\n".join(parts)

    def _pull_data_summary(self) -> tuple[str, str]:
        """Pull aggregated summaries from all 7 dashboard tables. Columns vary per table, so each
        summary is built dynamically (amount/name columns auto-detected). Returns
        (summary_text, table_info_text)."""
        summary_parts: list[str] = []
        table_info_parts: list[str] = []

        for label, table in self._settings.dashboard_tables.items():
            try:
                schema = self._get_table_schema(table)
                cols = [name for name, _ in schema]
                # Distinct categorical values go into table_info so the CALC-SQL builder (which only
                # receives table_info, not the summary) filters scopes on REAL values like
                # 'Event Invitee' / 'Fee for Service' instead of guessing '%CONGRESS%'/'%FFS%'.
                cat_lines = self._categorical_values_block(table, cols, self._engagement_where())
                info = self._table_info_line(table, schema)
                if cat_lines:
                    info = info + "\n" + "\n".join(cat_lines)
                table_info_parts.append(info)
                summary_parts.append(
                    self._dynamic_table_summary(f"{label.upper()} TABLE ({table})", table, cols)
                )
            except Exception as exc:
                summary_parts.append(f"{label.upper()} TABLE: Error pulling summary — {exc}")
                table_info_parts.append(f"Table: {table} (schema unavailable)")

        return "\n\n".join(summary_parts), "\n\n".join(table_info_parts)

    def _dynamic_table_summary(self, label: str, table: str, cols: list[str]) -> str:
        """Build a summary for a table by detecting amount and name columns dynamically.

        Every query is scoped to this audit's ENGAGEMENT_NUMBER(s) so the summary the AI judges on
        never mixes other audits'/countries' rows."""
        amt_col  = next((c for c in cols if any(k in c.lower() for k in ("value", "amount", "net"))), None)
        name_col = next((c for c in cols if any(k in c.lower() for k in ("customer", "name", "vendor", "supplier"))), None)
        where = self._engagement_where()

        lines = [f"{label}:", f"  Columns: {', '.join(cols)}"]

        try:
            row_count = self._run_sql(f"SELECT COUNT(*) as cnt FROM {table} {where}")
            if row_count:
                lines.append(f"  Total rows (this audit): {row_count[0].get('cnt', 0):,}")
        except Exception:
            pass

        if amt_col and name_col:
            try:
                top = self._run_sql(f"""
                    SELECT `{name_col}`,
                           COUNT(*) as txn_count,
                           ROUND(SUM(try_cast(`{amt_col}` AS DOUBLE)), 2) as total_value
                    FROM {table}
                    {where}
                    GROUP BY `{name_col}`
                    ORDER BY total_value DESC
                    LIMIT 15
                """)
                lines.append(f"  Top 15 by {amt_col}:")
                lines.append(self._format_table_rows(top, [name_col, "txn_count", "total_value"]))
            except Exception as exc:
                lines.append(f"  Aggregation error: {exc}")
        else:
            try:
                sample = self._run_sql(f"SELECT * FROM {table} {where} LIMIT 10")
                lines.append("  Sample rows (first 10):")
                if sample:
                    lines.append(self._format_table_rows(sample, list(sample[0].keys())))
            except Exception as exc:
                lines.append(f"  Sample error: {exc}")

        return "\n".join(lines)

    def _categorical_values_block(self, table: str, cols: list[str], where: str) -> list[str]:
        """List the REAL distinct values (with counts) of key categorical columns for this audit, so the
        calc-SQL builder filters scopes on actual values (e.g. CATEGORY='Fee for Service') not guesses."""
        cat_cols = [c for c in cols if any(h in c.lower() for h in _CATEGORICAL_NAME_HINTS)]
        lines: list[str] = []
        for col in cat_cols[:_MAX_CATEGORICAL_COLS]:
            try:
                rows = self._run_sql(
                    f"SELECT `{col}` AS val, COUNT(*) AS cnt FROM {table} {where} "
                    f"GROUP BY `{col}` ORDER BY cnt DESC LIMIT {_MAX_CATEGORICAL_VALUES}"
                )
            except Exception:
                continue
            vals = [
                f"{r.get('val')} ({r.get('cnt')})"
                for r in rows
                if r.get("val") not in (None, "")
            ]
            if vals:
                lines.append(f"  Distinct {col} values (this audit): " + "; ".join(vals))
        return lines

    def _get_table_columns(self, table: str) -> list[str]:
        """Return plain column names (no types)."""
        return [name for name, _type in self._get_table_schema(table)]

    def _get_table_schema(self, table: str) -> list[tuple[str, str]]:
        """Return (column_name, data_type) pairs from DESCRIBE, skipping partition markers."""
        rows = self._run_sql(f"DESCRIBE {table}")
        schema: list[tuple[str, str]] = []
        for r in rows:
            name = (r.get("col_name") or "").strip()
            if not name or name.startswith("#"):
                continue
            dtype = (r.get("data_type") or "").strip()
            schema.append((name, dtype))
        return schema

    def _table_info_line(self, table: str, schema: list[tuple[str, str]]) -> str:
        """Format a table's columns WITH their types so the LLM knows which are STRING
        (and must be wrapped in try_cast for numeric use)."""
        cols = ", ".join(f"`{name}` ({dtype})" for name, dtype in schema)
        return f"Table: {table}\nColumns: {cols}"

    # ──────────────────────────────────────────────────────────────────────────
    # Scope Calculations (execute the CPR Data Calculations per scope)
    # ──────────────────────────────────────────────────────────────────────────

    def _run_scope_calculations(
        self, scope_id: str, guidance: dict[str, str], table_info: str
    ) -> tuple[str, list[str]]:
        """Generate SQL from the scope's Data Calculations, execute it, and return the computed
        results as a text block together with the list of executed SQL queries. Returns ("", [])
        if nothing relevant could be computed."""
        queries = self._generate_calc_sql(scope_id, guidance, table_info)
        if not queries:
            logger.info("[%s] No scope calculations generated — falling back to generic summary | scope=%s",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id)
            return "", []

        blocks: list[str] = []
        executed_sqls: list[str] = []
        n_failed = 0
        n_with_rows = 0
        n_no_rows = 0
        for q in queries:
            purpose = (q.get("purpose") or "calculation").strip()
            sql = (q.get("sql") or "").strip()
            if not sql:
                continue
            sql = self._apply_limit(sql, 25)  # hard safety cap on computed rows
            executed_sql = sql
            # Friendly workflow name of the table this calculation runs against (shown per calc block).
            table_disp = self._workflow_display(self._extract_table_from_sql(sql)) or "Not Available"
            try:
                rows = self._run_sql(sql)
            except Exception as exc:
                # One automatic repair attempt: feed the error + the exact table schema back to the
                # LLM so it can fix an unresolved column / bad alias against the real columns, then
                # re-execute. This turns the common "hallucinated column" failure into a success.
                repaired = self._repair_calc_sql(scope_id, purpose, sql, str(exc), table_info)
                if repaired:
                    executed_sql = self._apply_limit(repaired, 25)
                    try:
                        rows = self._run_sql(executed_sql)
                        logger.info("[%s] Calc query repaired and executed | scope=%s | purpose=%s",
                                    AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose)
                    except Exception as exc2:
                        n_failed += 1
                        logger.warning("[%s] Calc query excluded (failed after repair) | scope=%s | "
                                       "purpose=%s | error=%s",
                                       AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose, exc2)
                        continue
                else:
                    # A query/tooling failure is NOT audit evidence. Exclude it from the block the AI
                    # judges on (and that becomes Calculation Evidence), keeping it in the log only, so
                    # a SQL error can never be promoted into a scored risk finding.
                    n_failed += 1
                    logger.warning("[%s] Calc query excluded (failed) | scope=%s | purpose=%s | error=%s",
                                   AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose, exc)
                    continue
            if not rows:
                n_no_rows += 1
                blocks.append(f"Calculation: {purpose}\nTable: {table_disp}\n  (no rows returned)")
                executed_sqls.append(executed_sql)
                continue
            n_with_rows += 1
            cols = list(rows[0].keys())
            summary = self._summarize_calc_rows(rows, cols)
            blocks.append(
                f"Calculation: {purpose}\nTable: {table_disp}\n"
                f"{summary}\n{self._format_table_rows(rows, cols)}"
            )
            executed_sqls.append(executed_sql)

        logger.info(
            "[%s] Scope calculations executed | scope=%s | queries=%d | with_rows=%d | no_rows=%d | failed_excluded=%d",
            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, len(queries), n_with_rows, n_no_rows, n_failed,
        )
        return _CALC_BLOCK_SEPARATOR.join(blocks), executed_sqls

    def _repair_calc_sql(
        self, scope_id: str, purpose: str, failed_sql: str, error: str, table_info: str
    ) -> str:
        """Ask the LLM to fix a failed calc query against the real schema. Returns repaired SQL, or
        "" if it could not be repaired. Best-effort — never raises."""
        user_msg = CALC_SQL_REPAIR_TEMPLATE.format(
            scope_area=scope_id,
            purpose=purpose,
            engagement_filter=self._engagement_filter,
            failed_sql=failed_sql,
            error=error,
            table_info=table_info or "Table schema not available.",
        )
        try:
            response = self._llm.invoke([
                SystemMessage(content=CALC_SQL_REPAIR_SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ])
            raw = (response.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.lower().startswith("sql"):
                    raw = raw[3:]
                raw = raw.strip()
            # Guard against a non-SQL refusal / empty answer.
            if not raw or "select" not in raw.lower():
                return ""
            return raw
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Calc SQL repair failed | scope=%s | purpose=%s | error=%s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose, exc)
            return ""

    def _generate_calc_sql(self, scope_id: str, guidance: dict[str, str], table_info: str) -> list[dict]:
        """Ask the LLM to turn the scope's Data Calculations into concrete SQL queries."""
        user_msg = CALC_SQL_USER_TEMPLATE.format(
            scope_area=scope_id,
            engagement_filter=self._engagement_filter,
            category_filter=self._resolve_scope_category_filter(scope_id)
                or "(none — infer the activity filter from the real CATEGORY values listed in the table info)",
            data_calculations=guidance.get("data_calculations")
                or "Not available — use your own FCPA audit intelligence to decide what to compute.",
            red_flags=guidance.get("red_flags") or "Not available.",
            table_info=table_info or "Table schema not available.",
        )
        # Tenders only: append the IDR tender-list so the Payments cross-check can target real entities.
        tender_ctx = self._tender_context_for(scope_id)
        if tender_ctx:
            user_msg = f"{user_msg}\n\n{tender_ctx}"
        try:
            response = self._llm.invoke([
                SystemMessage(content=CALC_SQL_SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ])
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            parsed = json.loads(raw)
            queries = parsed.get("queries", []) if isinstance(parsed, dict) else []
            return [q for q in queries if isinstance(q, dict) and q.get("sql")]
        except Exception as exc:
            logger.warning("[%s] Calc SQL generation failed | scope=%s | error=%s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # AI Analysis
    # ──────────────────────────────────────────────────────────────────────────

    def _analyze_with_ai(
        self,
        scope_id: str,
        guidance: dict[str, str],
        data_summary: str,
        table_info: str,
    ) -> dict:
        user_msg = ANALYSIS_USER_TEMPLATE.format(
            scope_area=scope_id,
            engagement_filter=self._engagement_filter,
            scope_description=guidance.get("scope_description") or "Not available for this scope area.",
            red_flags=guidance.get("red_flags") or "Not available for this scope area.",
            data_calculations=guidance.get("data_calculations") or "Not available for this scope area.",
            sampling_rationale=guidance.get("sampling_rationale") or "Not available for this scope area.",
            expected_sample_size=guidance.get("expected_sample_size") or "Not available for this scope area.",
            table_info=table_info or "Table schema not available.",
            data_summary=data_summary or "No transactional data available.",
        )
        # Append the auditor's TPI inventory so the analysis can match suppliers/payees to
        # in-scope TPIs (e.g. GL-mismatch: is this vendor an in-scope TPI?).
        if self._tpi_inventory_block:
            user_msg = f"{user_msg}\n\n{self._tpi_inventory_block}"
        # Append the HCC GL account map so GL codes are verified against the approved mapping.
        if self._gl_account_map_block:
            user_msg = f"{user_msg}\n\n{self._gl_account_map_block}"
        # Append the MAP/GACS remediation register so an already-closed finding is not re-raised as open.
        if self._remediation_register_block:
            user_msg = f"{user_msg}\n\n{self._remediation_register_block}"
        # Tenders only: append the IDR tender-list so the judgment can reason over the tendering entities.
        tender_ctx = self._tender_context_for(scope_id)
        if tender_ctx:
            user_msg = f"{user_msg}\n\n{tender_ctx}"

        try:
            response = self._llm.invoke([
                SystemMessage(content=ANALYSIS_SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ])
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            parsed = json.loads(raw)
            # Prompt returns a single consolidated object per scope. Tolerate a stray
            # {"risks": [...]} shape by taking the first (most material) entry.
            if isinstance(parsed, dict) and isinstance(parsed.get("risks"), list):
                parsed = parsed["risks"][0] if parsed["risks"] else {}
            if not isinstance(parsed, dict):
                parsed = {}
            logger.info("[%s] AI assessment | scope=%s | risk_type=%s",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id, parsed.get("risk_type") or "none")
            return parsed
        except Exception as exc:
            logger.error("[%s] AI analysis failed | scope=%s | error=%s",
                         AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return {}

    # ──────────────────────────────────────────────────────────────────────────
    # Sample Rows
    # ──────────────────────────────────────────────────────────────────────────

    def _pull_samples(self, sample_query: str, limit: int) -> str:
        """Execute the LLM-generated sample query and format rows as bullet points.
        The projection is forced to SELECT * so each sample includes the FULL transaction
        record (all columns), and the row count is forced to `limit`."""
        if not sample_query or not sample_query.strip():
            return ""
        query = self._force_select_star(sample_query)
        query = self._apply_limit(query, limit)
        try:
            rows = self._run_sql(query)
            if not rows:
                return "No sample rows matched the query."
            return self._format_samples(rows)
        except Exception as exc:
            logger.warning("[%s] Sample query failed | error=%s | query=%s",
                           AGENT_TRANSACTIONAL_ANALYSIS, exc, query[:200])
            return f"Sample query failed: {exc}"

    @staticmethod
    def _parse_sample_size(text: str) -> int:
        """Extract the expected sample size from CPR column K free text.
        Handles single numbers ("25"), ranges ("3-5 invoices" → upper bound 5), and
        phrasings like "Minimum 20". Ignores percentages and falls back to
        _DEFAULT_SAMPLE_SIZE when no usable absolute count is present."""
        if not text:
            return _DEFAULT_SAMPLE_SIZE
        # Drop percentages ("10%") — not a usable absolute row count.
        cleaned = re.sub(r"\d+\s*%", " ", text)
        # A range like "3-5" / "3 – 5" / "3 to 5" → take the upper bound.
        range_match = re.search(r"(\d+)\s*(?:-|–|—|to)\s*(\d+)", cleaned)
        if range_match:
            return max(int(range_match.group(1)), int(range_match.group(2)))
        num_match = re.search(r"\d+", cleaned)
        if num_match:
            return int(num_match.group())
        return _DEFAULT_SAMPLE_SIZE

    @staticmethod
    def _apply_limit(sql: str, limit: int) -> str:
        """Force the result-set size to `limit`, overriding any trailing LIMIT the LLM
        wrote (or appending one if absent)."""
        stripped = sql.rstrip().rstrip(";").rstrip()
        trailing_limit = re.compile(r"(?is)\blimit\b\s+\d+\s*$")
        if trailing_limit.search(stripped):
            return trailing_limit.sub(f"LIMIT {limit}", stripped)
        return f"{stripped} LIMIT {limit}"

    @staticmethod
    def _force_select_star(sql: str) -> str:
        """Rewrite the outer projection to SELECT * so samples include every column of the
        transaction row. Keeps the WHERE/ORDER BY intact (the prompt orders by real columns,
        not SELECT-list aliases, so this stays valid)."""
        new_sql, n = re.subn(r"(?is)^\s*select\s+.*?\s+from\s", "SELECT * FROM ", sql, count=1)
        return new_sql if n else sql

    @staticmethod
    def _format_samples(rows: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for i, row in enumerate(rows, 1):
            parts = []
            for k, v in row.items():
                if v is None:
                    continue
                if isinstance(v, float):
                    v = f"{v:,.2f}"
                parts.append(f"{k}: {v}")
            lines.append(f"• Sample {i}: {' | '.join(parts)}")
            if i < len(rows):
                lines.append(_SAMPLE_SEPARATOR)
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────────
    # RiskOutput Builder
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _coerce_axis(value) -> int:
        """Coerce an LLM-supplied Likelihood/Impact value to an int (0 if absent/invalid)."""
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    def _to_risk_output(
        self, scope_id: str, finding: dict, samples: str, calculation_evidence: str = "",
        calc_sqls: list[str] | None = None,
    ) -> RiskOutput:
        risk_type_str = (finding.get("risk_type") or "No Risk Identified").strip()
        if "potential" in risk_type_str.lower():
            risk_title = RiskType.POTENTIAL_RISK
        elif "further" in risk_type_str.lower():
            risk_title = RiskType.FURTHER_REVIEW
        else:
            risk_title = RiskType.NO_RISK

        # Score on the SAME Likelihood x Impact matrix as the classifier — and apply the classifier's
        # STRICT Type→Category binding (risk_classifier_prompt STEP 10): ONLY a Potential Risk is
        # scored and banded. Further Review Required is an OPEN item pending fieldwork — it is NOT
        # scored, carries no band, and its category is "Not Applicable". No Risk (or missing/invalid
        # axes) is likewise unscored → "No Risk". This stops a data-quality / statutory / unfed-column
        # driven finding (which the prompt routes to Further Review Required) from being banded Medium/
        # Low; as "Not Applicable" it is excluded from the banded Output C view, exactly like the
        # questionnaire path.
        likelihood = self._coerce_axis(finding.get("likelihood"))
        impact = self._coerce_axis(finding.get("impact"))
        if risk_title == RiskType.FURTHER_REVIEW:
            likelihood, impact, matrix_score = 0, 0, 0
            risk_category = RISK_CATEGORY_NOT_CATEGORIZED
        elif risk_title == RiskType.NO_RISK or likelihood < 1 or impact < 1:
            likelihood, impact, matrix_score = 0, 0, 0
            risk_category = RISK_CATEGORY_NONE
        else:
            ms = score_risk(impact, likelihood)
            likelihood, impact, matrix_score = ms.likelihood, ms.impact, ms.score
            risk_category = ms.category

        # Exact table name: prefer the real FROM-clause table of the query that ran; then the
        # LLM's stated source_table (if it looks real); finally the configured tables.
        source_table = self._extract_table_from_sql(finding.get("sample_query", "") or "")
        if not source_table:
            candidate = (finding.get("source_table") or "").strip()
            if candidate and candidate.lower() != "databricks transactional tables":
                source_table = candidate
        if not source_table:
            source_table = ", ".join(self._settings.dashboard_tables.values())

        description = (finding.get("risk_description") or "").strip() or "AI-identified risk from transactional data analysis."
        rationale   = (finding.get("risk_rationale") or "").strip() or "Per HCBI/FCPA compliance framework."
        evidence    = (finding.get("evidence_explanation") or "").strip() or "Identified via AI-driven transactional data analysis."
        score_rsn   = (finding.get("risk_score_reasoning") or "").strip() or (
            f"Likelihood {likelihood} x Impact {impact} = {matrix_score} ({risk_category})."
            if matrix_score else "No transactional risk scored."
        )
        title_str   = (finding.get("risk_title") or risk_type_str).strip()

        sql_parts = list(calc_sqls or [])
        sample_query = (finding.get("sample_query") or "").strip()
        if sample_query and sample_query not in sql_parts:
            sql_parts.append(sample_query)
        sql_queries = _CALC_BLOCK_SEPARATOR.join(sql_parts) or None

        dashboard_source = self._dashboard_source_name(source_table)

        return RiskOutput(
            risk_title=risk_title,
            risk_type=title_str,
            likelihood=likelihood,
            impact=impact,
            matrix_score=matrix_score,
            risk_category=risk_category,
            risk_score_reasoning=score_rsn,
            # Use the canonical display name (same map the questionnaire path applies) so Output A/C
            # show the agreed scope label and aggregation groups transactional + questionnaire rows of
            # the SAME scope together, not as two buckets (raw id vs display name). Falls back to the
            # raw id if the scope is not in the map.
            scope_area=SCOPE_ID_TO_DISPLAY_NAME.get(scope_id, scope_id),
            risk_description=description,
            risk_rationale=rationale,
            evidence_explanation=evidence,
            calculation_evidence=(calculation_evidence.strip() or None),
            sql_queries=sql_queries,
            samples=samples,
            source_details=[SourceDetail(
                source_name="Transactional Data Analysis",
                document_name=dashboard_source,
                section="AI Risk Identification",
            )],
        )

    @classmethod
    def _dashboard_source_name(cls, table_ref: str) -> str:
        """Name the validating dashboard(s) for a citation, derived from the queried table(s).

        Audit-agnostic: the dashboard is taken from the table actually queried, so the correct
        dashboard is cited for any audit without naming a specific export file."""
        display = cls._workflow_display(table_ref)
        if not display:
            return display
        return ", ".join(
            name if name.lower().endswith("dashboard") else f"{name} Dashboard"
            for name in display.split(", ")
        )

    @staticmethod
    def _workflow_display(table_ref: str) -> str:
        """Map raw Gold-layer table name(s) to friendly workflow names for source display.

        Accepts a single name, a fully-qualified name, or a comma-separated list; strips the
        catalog/schema prefix, maps each base name, and de-duplicates while preserving order.
        """
        if not table_ref:
            return table_ref
        names: list[str] = []
        for part in table_ref.split(","):
            base = part.strip().strip("`").split(".")[-1].strip().strip("`")
            display = DASHBOARD_WORKFLOW_NAMES.get(base, base)
            if display not in names:
                names.append(display)
        return ", ".join(names)

    @staticmethod
    def _extract_table_from_sql(sql: str) -> str:
        """Return the fully-qualified table name from the FROM clause of a query (or "")."""
        if not sql:
            return ""
        m = re.search(r"\bfrom\s+([A-Za-z0-9_.`]+)", sql, re.IGNORECASE)
        return m.group(1).strip().replace("`", "") if m else ""

    # ──────────────────────────────────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────────────────────────────────

    def _local_reference_path(self, path_str: str) -> Path | None:
        p = Path(path_str)
        if p.exists():
            return p

        volume_path = str(path_str).replace("\\", "/")
        if not volume_path.startswith("/Volumes/"):
            return None

        cache_dir = Path(tempfile.gettempdir()) / "audit_phase3_refs"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_path = cache_dir / Path(volume_path).name
        if local_path.exists() and local_path.stat().st_size > 0:
            return local_path

        ok = download_volume_file(
            self._settings.databricks_host,
            bearer_token(self._settings),
            volume_path,
            local_path,
        )
        return local_path if ok else None

    def _run_sql(self, sql: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            return [dict(zip(cols, row)) for row in result.fetchall()]

    @staticmethod
    def _format_table_rows(
        rows: list[dict[str, Any]],
        cols: list[str],
        title: str = "",
    ) -> str:
        lines: list[str] = []
        if title:
            lines.append(title)
            lines.append("-" * len(title))
        for i, row in enumerate(rows, start=1):
            parts = []
            for col in cols:
                val = row.get(col, "N/A")
                if isinstance(val, float):
                    val = f"{val:,.2f}"
                parts.append(f"{col}: {val}")
            lines.append(f"  {i}. {' | '.join(parts)}")
        return "\n".join(lines)

    @staticmethod
    def _enforce_category_filter(sql: str, category_filter: str) -> str:
        """Guarantee `category_filter` appears in the WHERE clause.
        If the LLM already included it (checked by column name), return sql unchanged.
        Otherwise inject it before GROUP BY (or at the end of the WHERE block)."""
        col_name = category_filter.split()[0]
        if col_name.lower() in sql.lower():
            return sql
        group_by = re.search(r"\bGROUP\s+BY\b", sql, re.IGNORECASE)
        if group_by:
            pos = group_by.start()
            return sql[:pos].rstrip() + f"\n  AND {category_filter}\n" + sql[pos:]
        return sql.rstrip() + f"\n  AND {category_filter}"

    @staticmethod
    def _coerce_number(value) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.replace(",", "").replace("$", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    @classmethod
    def _summarize_calc_rows(cls, rows: list[dict[str, Any]], cols: list[str]) -> str:
        """One-line roll-up of a calculation result: row count plus the sum of each MEASURE column.

        Identifier/code/period columns (SUPPLIER_ID, COMPANY_CODE, *_NUMBER, dates) are skipped — summing
        them is meaningless."""
        totals: list[str] = []
        for col in cols:
            if _NON_MEASURE_COL_RE.search(col):
                continue
            numbers = [n for n in (cls._coerce_number(r.get(col)) for r in rows) if n is not None]
            if numbers:
                totals.append(f"{col}={sum(numbers):,.2f}")
        summary = f"Summary: {len(rows)} row(s)"
        if totals:
            summary += "; " + "; ".join(totals)
        return summary

    @staticmethod
    def _build_engine(settings: Settings):
        # Pool sized for concurrent per-scope analysis (run_all uses a thread pool).
        return build_engine(
            settings,
            schema=settings.databricks_schema_name,
            pool_size=_SCOPE_ANALYSIS_WORKERS + 2,
            max_overflow=_SCOPE_ANALYSIS_WORKERS,
            pool_pre_ping=True,
        )
