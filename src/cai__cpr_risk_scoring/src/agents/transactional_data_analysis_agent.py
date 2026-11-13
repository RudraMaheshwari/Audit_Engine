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

_CPR_COL_SCOPE_DESC    = 3
_CPR_COL_RED_FLAGS     = 4
_CPR_COL_DATA_CALCS    = 9
_CPR_COL_SAMPLING_RAT  = 10
_CPR_COL_SAMPLE_SIZE   = 11
_CPR_HEADER_ROWS       = 3

_SAMPLE_SEPARATOR = "─" * 60
_CALC_BLOCK_SEPARATOR = "\n" + "─" * 60 + "\n"

_DEFAULT_SAMPLE_SIZE = 7

_SCOPE_ANALYSIS_WORKERS = 6

_CATEGORICAL_NAME_HINTS = ("category", "type", "classification", "status", "government_vendors")
_MAX_CATEGORICAL_COLS = 6
_MAX_CATEGORICAL_VALUES = 40

_UPPER_IDENT_RE = re.compile(r"[A-Z][A-Z0-9_]{2,}")
_SQL_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_APOSTROPHE_VARIANTS_RE = re.compile("[’‘ʼʻ`´＇]")
_FROM_JOIN_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_.`]+)", re.IGNORECASE)
_AS_ALIAS_RE = re.compile(r"\bAS\s+`?([A-Za-z0-9_]+)`?", re.IGNORECASE)
_CTE_NAME_RE = re.compile(r"(?:\bWITH\b|,)\s*([A-Za-z0-9_]+)\s+AS\s*\(", re.IGNORECASE)
_SQL_RESERVED_TOKENS = frozenset({
    "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "BY", "HAVING", "LIMIT", "OFFSET", "DISTINCT",
    "AS", "AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE", "ILIKE", "RLIKE", "BETWEEN", "EXISTS",
    "CASE", "WHEN", "THEN", "ELSE", "END", "ON", "USING", "JOIN", "INNER", "LEFT", "RIGHT", "FULL",
    "OUTER", "CROSS", "WITH", "UNION", "ALL", "INTERSECT", "EXCEPT", "ASC", "DESC", "OVER",
    "PARTITION", "ROWS", "RANGE", "UNBOUNDED", "PRECEDING", "FOLLOWING", "CURRENT", "ROW", "NULLS",
    "FIRST", "LAST", "QUALIFY",
    "SUM", "COUNT", "AVG", "MIN", "MAX", "COALESCE", "NULLIF", "CAST", "TRY_CAST", "UPPER", "LOWER",
    "TRIM", "LTRIM", "RTRIM", "LENGTH", "SUBSTR", "SUBSTRING", "CONCAT", "CONCAT_WS", "REPLACE",
    "ROUND", "ABS", "FLOOR", "CEIL", "CEILING", "DATEDIFF", "DATE_ADD", "DATE_SUB", "TO_DATE",
    "DATE_FORMAT", "YEAR", "MONTH", "DAY", "QUARTER", "WEEK", "HOUR", "MINUTE", "SECOND", "NOW",
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "EXTRACT", "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD",
    "GREATEST", "LEAST", "IF", "IFNULL", "DECODE", "COLLECT_LIST", "COLLECT_SET", "ARRAY", "MAP",
    "STRUCT", "EXPLODE", "SPLIT", "REGEXP_REPLACE", "REGEXP_EXTRACT", "INSTR", "LOCATE", "POSITION",
    "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC",
    "STRING", "VARCHAR", "CHAR", "TEXT", "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "BINARY",
    "TRUE", "FALSE", "KG",
})

_NON_MEASURE_COL_RE = re.compile(
    r"(^|_)(id|code|number|no|key|guid|uuid|zip|year|month|quarter|date)(_|$)", re.IGNORECASE
)

_HCC_SCOPE_CATEGORY_VALUES: dict[str, list[str]] = {
    "FEE_FOR_SERVICE__DOCTOR_CONSULTING": ["Lecture Fee", "Consulting Fee"],
    "SPONSORSHIPS":                       ["Booth&Advertisement", "Sponsorship for the 3rd Party Congress"],
    "CONGRESSES__JJ_ORGANIZED_EVENTS":    ["Sponsorship for HCP's attendance to the 3rd Party Congress"],
    "EDUCATIONAL_GRANTS":                 ["Educational Grants / Campaign Donation"],
    "DONATIONS":                          ["Contribution incl. Charitable Contribution"],
    "MARKETING_RESEARCH_PROJECTS":        ["Market Research"],
}
_HCC_CATEGORY_TABLE_KEYS = ("hcc_full", "hcc")
_HCC_CATEGORY_COLUMNS = ("CATEGORY", "REQUEST_TYPE")

_SCOPE_CATEGORY_RESOLVER_MAP: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "CONGRESSES__JJ_ORGANIZED_EVENTS": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("congresses",)),
        ("CATEGORY",                               ("event invitee",)),
        ("REQUEST_TYPE",                           ("congress", "organized event", "event invitee", "event")),
    ],
    "FEE_FOR_SERVICE__DOCTOR_CONSULTING": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("fee for service",)),
        ("CATEGORY",                               ("fee for service",)),
        ("REQUEST_TYPE",                           ("fee for service", "consulting")),
    ],
    "EDUCATIONAL_GRANTS": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("educational grant", "research grant")),
        ("CATEGORY",                               ("grant",)),
        ("REQUEST_TYPE",                           ("educational grant", "research grant", "grant")),
    ],
    "SPONSORSHIPS": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("promotional sponsorship",)),
        ("CATEGORY",                               ("event invitee",)),
        ("REQUEST_TYPE",                           ("promotional sponsorship", "sponsorship")),
    ],
    "DONATIONS": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("donation", "contribution", "charitable")),
        ("CATEGORY",                               ("donation", "contribution", "charitable")),
        ("REQUEST_TYPE",                           ("donation", "contribution", "charitable")),
    ],
    "MARKETING_RESEARCH_PROJECTS": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("market research",)),
        ("CATEGORY",                               ("market research",)),
        ("REQUEST_TYPE",                           ("market research",)),
    ],
    "VALUE_ADD__PATIENT_SUPPORT_PROG": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("patient support", "psp")),
        ("CATEGORY",                               ("patient support", "psp", "value add")),
        ("REQUEST_TYPE",                           ("patient support", "psp", "value add")),
    ],
    "PRODUCT_TRAINING": [
        ("AUDIT_AREAS_CATEGORIZED_BY_GL_ACCOUNTS", ("product training", "training")),
        ("CATEGORY",                               ("product training", "training")),
        ("REQUEST_TYPE",                           ("product training", "training")),
    ],
    "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS": [
        ("TRANSACTION_TYPE", ("sale",)),
    ],
    "PRICE_OVERRIDES": [
        ("TRUE_PRICE_OVERRIDE", ("y",)),
    ],
    "REBATES_PAID_TO_DISTRIBUTORS": [
        ("ORDER_REASON_DESCRIPTION", ("rebate",)),
    ],
    "FREE_GOODS": [
        ("TRANSACTION_TYPE", ("free good",)),
    ],
    "PRODUCT_SAMPLES": [
        ("SALE_TYPE_DESCRIPTION", ("sample",)),
    ],
    "CREDIT_NOTES": [
        ("TRANSACTION_TYPE", ("credit memo",)),
    ],
    "EXPENSE_REPORTS": [
        ("RPT_ENT_EXP_TYPE_NM", ("hcp", "meal", "symposium", "sponsorship", "registration")),
    ],
}


_SCOPE_FALLBACK_DASHBOARD: dict[str, tuple[str, ...]] = {
    "CONTRACT_SALES_AGENTS":                        ("payment", "disbursement"),
    "EDUCATIONAL_GRANTS":                           ("payment", "disbursement"),
    "FEE_FOR_SERVICE__DOCTOR_CONSULTING":           ("payment", "disbursement"),
    "PAYMENTS_IN_AP_TO_DISTRIBUTORS":               ("payment", "disbursement"),
    "REBATES_PAID_TO_DISTRIBUTORS":                 ("payment", "disbursement"),
    "SPONSORSHIPS":                                 ("payment", "disbursement"),
    "VALUE_ADD__PATIENT_SUPPORT_PROG":              ("payment", "disbursement"),
    "CONTRACT_MANUFACTURING_VENDORS":               ("payment", "disbursement"),
    "FORMER_GOVERNMENT_EMPLOYEES":                  ("payment", "disbursement"),
    "INTERACTION_WITH_PRODUCT_AND_SITE_REGULATORS": ("payment", "disbursement"),
    "LEGAL_FEES":                                   ("payment", "disbursement"),
    "LOBBYISTS":                                    ("payment", "disbursement"),
    "LOCAL_TRAVEL_AGENCIES":                        ("payment", "disbursement"),
    "LOGISTICS_INTERMEDIARIES__CUSTOMS":            ("payment", "disbursement"),
    "MARKETING_RESEARCH_PROJECTS":                  ("payment", "disbursement"),
    "MEDIA_AND_PUBLIC_RELATIONS_VENDORS":           ("payment", "disbursement"),
    "PAYMENTS_TO_GOVERNMENT_AGENCIES":              ("payment", "disbursement"),
    "PAYMENTS_TO_GOVERNMENT_OFFICIALS":             ("payment", "disbursement"),
    "PROCUREMENT_FROM_GOVERNMENT_OWNED_ENTITIES":   ("payment", "disbursement"),
    "PRODUCT_PROMOTION_VIA_THIRD_PARTY":            ("payment", "disbursement"),
    "ROYALTIES_PAID_TO_PUBLIC_HCPS":                ("payment", "disbursement"),
    "TRAINING_FOR_POLICYMAKERS":                    ("payment", "disbursement"),
    "PATIENT_ADVOCACY":                             ("payment", "disbursement"),
    "STRATEGIC_AFFAIRS":                            ("payment", "disbursement"),
    "ONE_TIME_VENDOR":                              ("payment", "disbursement"),
    "CLINICAL_TRIALS":                              ("payment", "disbursement"),
    "CASH_ADVANCES__PETTY_CASH__MANUAL_CHECKS":     ("payment", "disbursement"),
    "FREE_GOODS":                                   ("revenue",),
    "PRODUCT_SAMPLES":                              ("revenue",),
    "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS":    ("revenue",),
    "PRICE_OVERRIDES":                              ("revenue",),
    "CREDIT_NOTES":                                 ("revenue",),
    "EXPENSE_REPORTS":                              ("travel_expense",),
    "GOVERNMENT_AFFAIRS":                           ("travel_expense",),
    "MARKET_ACCESS":                                ("travel_expense",),
}


_LEGAL_SUFFIX_TOKENS = {
    "PTY", "LTD", "LIMITED", "INC", "INCORPORATED", "LLC", "PLC", "CO", "CORP", "CORPORATION",
    "COMPANY", "COMPANIES", "LLP", "LP",
    "GMBH", "MBH", "BV", "NV", "SA", "AG", "AB", "AS", "OY", "SRL", "SPA", "SAS", "SARL", "SASU",
    "SL", "KG", "OHG", "KFT", "ZRT", "EOOD", "OOD", "AD", "DOO", "DOOEL", "OOO", "SP", "ZOO",
    "APS", "APB", "KS", "SC", "SE",
    "PT", "TBK", "PERSERO", "PERSEROAN", "TERBATAS", "TERBUKA", "CV", "UD", "FA",
    "PTE", "SDN", "BHD", "BERHAD", "KK", "GK", "YK", "LLK",
    "FZE", "FZCO", "FZ", "DMCC",
}

_CONNECTOR_TOKENS = {"AND", "THE", "OF", "DE", "DI", "DU", "DA", "DAN", "EN", "ET", "UND"}

_DROP_TOKENS = _LEGAL_SUFFIX_TOKENS | _CONNECTOR_TOKENS


_CJK_RANGES = "가-힣ᄀ-ᇿ㄰-㆏一-鿿぀-ヿ"
_CJK_RE = re.compile(f"[{_CJK_RANGES}]")


def _normalize_entity(name) -> str:
    """Normalise an entity/payee name for suffix/punctuation/case-insensitive matching.

    Fold accents to ASCII (NESTLÉ -> NESTLE) so Latin variants align; drop punctuation and legal-form /
    connector tokens (wherever they sit). KEEPS region/qualifier tokens such as 'NZ' (from '(NZ)') so
    genuinely different legal entities — e.g. IQVIA (NZ) vs IQVIA Australia — are NOT merged. Hangul/CJK
    characters are PRESERVED so a Korean payee can match a Korean TPI name (the inventory loads both the
    English and local-language names)."""
    import re as _re
    import unicodedata as _ud
    raw = str(name) if name is not None else ""
    folded = "".join(c for c in _ud.normalize("NFKD", raw) if not _ud.combining(c))
    s = folded.upper()
    s = _re.sub(rf"[^A-Z0-9{_CJK_RANGES} ]+", " ", s)
    tokens = [t for t in s.split() if t and t not in _DROP_TOKENS]
    return " ".join(tokens)


_SCOPE_DOC_SOURCES: dict[str, list[str]] = {
    "DUE_DILIGENCE":                   ["TPI Inventory List"],
    "TENDERS":                         ["IDR Tender Report (Tab 4, Q68)"],
    "VALUE_ADD__PATIENT_SUPPORT_PROG": ["PSP List (IDR)"],
    "PAYMENTS_IN_AP_TO_DISTRIBUTORS":  ["Distributor List (IDR)"],
}

_NAME_LIST_SCOPES: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "DUE_DILIGENCE":                   ((), ("disbursement", "payment", "revenue"), "TPI Inventory List"),
    "PAYMENTS_IN_AP_TO_DISTRIBUTORS":  (("distributor",), ("disbursement", "payment"), "Distributor List (IDR)"),
    "VALUE_ADD__PATIENT_SUPPORT_PROG": (("psp", "patient support", "patient-support"), ("disbursement", "payment", "hcc"), "PSP List (IDR)"),
    "TENDERS":                         (("tender",), ("disbursement", "payment"), "IDR Tender Report (Tab 4, Q68)"),
}

_TPI_PAYMENT_CALC_SCOPES: dict[str, str] = {
    "DUE_DILIGENCE":                  "in-scope third parties (TPI Inventory List)",
    "PAYMENTS_IN_AP_TO_DISTRIBUTORS": "in-scope distributors (Distributor List)",
}

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
    txn_count: int = 0
    total_usd: float = 0.0
    usd_by_date: dict[str, float] = field(default_factory=dict)
    table_sources: list[str] = field(default_factory=list)
    doc_txn_count: int = 0
    doc_total_usd: float = 0.0
    doc_usd_by_date: dict[str, float] = field(default_factory=dict)
    doc_sources: list[str] = field(default_factory=list)
    has_data: bool = False
    amount_display_override: str | None = None
    payee_breakdown: list = field(default_factory=list)


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

    def __init__(
        self, llm: BaseChatModel, settings: Settings, tpi_names: list[str] | None = None
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._engine = self._build_engine(settings)
        self._distinct_values_cache: dict[tuple[str, str], list[str]] = {}
        if tpi_names is None:
            tpi_names = load_tpi_inventory_names(settings, llm=llm)
        self._tpi_names = [n for n in (tpi_names or []) if str(n).strip()]
        self._tpi_inventory_block = format_tpi_inventory_block(tpi_names, settings.tpi_inventory_sheet)
        gl_mapping = load_gl_account_map(settings)
        self._gl_account_map_block = format_gl_account_map_block(gl_mapping)
        logger.info(
            "[%s] Compiled  tables=%s | tpi_inventory_names=%d (sheet=%s) | gl_account_mappings=%d",
            AGENT_TRANSACTIONAL_ANALYSIS,
            list(settings.dashboard_tables.values()),
            len(tpi_names), settings.tpi_inventory_sheet, len(gl_mapping),
        )


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

    @cached_property
    def _scope_period_filter(self) -> str:
        """Runtime instruction bounding every generated query to the audit scope period. The date
        COLUMN differs per dashboard table, so this is phrased as a directive the model applies on each
        table's own transaction/posting/event date column — not a single fixed SQL predicate. Returns a
        'no period configured' note when neither bound is set (so no date filter is applied)."""
        start = (self._settings.scope_period_start or "").strip()
        end = (self._settings.scope_period_end or "").strip()
        apply_hint = ("Apply on each table using THAT table's own transaction/posting/event date column "
                      "wrapped in try_cast(`col` AS DATE); exclude any row dated outside this range.")
        if start and end:
            return (f"Include ONLY rows whose transaction/posting/event date is BETWEEN {start} AND {end} "
                    f"(inclusive). {apply_hint}")
        if start:
            return f"Include ONLY rows whose transaction/posting/event date is ON OR AFTER {start}. {apply_hint}"
        if end:
            return f"Include ONLY rows whose transaction/posting/event date is ON OR BEFORE {end}. {apply_hint}"
        return "(no explicit scope period configured — apply no date bound)"

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
        except Exception as exc:
            msg = str(exc)
            if "UNRESOLVED_COLUMN" in msg or "cannot be resolved" in msg:
                logger.debug("[%s] Column %s not on %s — skipping (table-agnostic scan)",
                             AGENT_TRANSACTIONAL_ANALYSIS, column, table_key)
            else:
                logger.warning("[%s] Could not read distinct %s.%s | %s",
                               AGENT_TRANSACTIONAL_ANALYSIS, table_key, column, msg.splitlines()[0][:200])
            values = []
        self._distinct_values_cache[cache_key] = values
        return values

    def _distinct_values_all_tables(self, column: str, table_keys=None) -> list[str]:
        """Distinct values of `column`, engagement-scoped, unioned + de-duplicated. By default scans
        EVERY active dashboard table that carries the column (table-agnostic); pass `table_keys` to
        restrict the scan to a specific subset (used for HCC-first resolution). Tables lacking the
        column contribute nothing."""
        keys = table_keys if table_keys is not None else list(self._settings.dashboard_tables)
        seen: set[str] = set()
        out: list[str] = []
        for table_key in keys:
            for v in self._distinct_column_values(table_key, column):
                key = v.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    out.append(v.strip())
        return out


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
        except Exception as exc:
            logger.warning("[%s] Tender-list retrieval skipped | %s", AGENT_TRANSACTIONAL_ANALYSIS, exc)
            return ""

    def _tender_context_for(self, scope_id: str) -> str:
        """The tender-list block for the Tenders scope only; '' for every other scope."""
        resolved = _resolve_scope_id(scope_id.replace("_", " ")) or scope_id.strip().upper()
        return self._tender_list_block if resolved == "TENDERS" else ""

    def _tpi_payment_calc_block(self, scope_id: str) -> tuple[str, str]:
        """Deterministic 'payments made to the in-scope named parties' calculation for a name-list
        payment scope (Due Diligence via the TPI inventory; AP-to-distributors via the distributor
        list). Reuses the EXACT token matcher Output F uses (``_name_list_summary``), so this calc
        figure and the Output F — Scope Transaction Summary figure are guaranteed identical (same
        names, same cosmetic-tolerant matching, same AP superset rule). Returns
        (block_text, representative_sql), or ("", "") for any other scope / when no names resolve.

        This is why the two are no longer 'separate': the generic LLM calcs still show the AP
        population (Janssen etc. — vendors that are NOT TPIs), while THIS block isolates only the
        payments whose payee matches a party on the TPI Inventory List."""
        resolved = _resolve_scope_id(scope_id.replace("_", " ")) or scope_id.strip().upper()
        label = _TPI_PAYMENT_CALC_SCOPES.get(resolved)
        if not label:
            return "", ""
        cfg = _NAME_LIST_SCOPES.get(resolved)
        if not cfg:
            return "", ""
        keywords, table_keys, doc_label = cfg
        names = [str(n).strip() for n in self._name_list_for_scope(resolved, keywords) if str(n).strip()]
        if not names:
            return "", ""
        summary = self._name_list_summary(resolved, names, table_keys, doc_label)
        sources = "; ".join(summary.table_sources) if summary.table_sources else "Not Available"
        block = (
            f"Calculation: Payments made to {label} — payees matched against the {doc_label} "
            f"({len(names)} listed parties), token-matched on the SAME basis as Output F (Scope "
            f"Transaction Summary) so the two figures are identical. The other calculations above cover "
            f"the whole AP population (which includes ordinary vendors that are NOT in-scope parties); "
            f"this one isolates only payments to the listed parties.\n"
            f"Table: {sources}\n"
            f"Summary (in-scope payees only): matched_payment_count={summary.txn_count}; "
            f"total_usd_paid_to_in_scope_parties={summary.total_usd:,.2f}"
        )
        breakdown = summary.payee_breakdown or []
        if breakdown:
            shown = breakdown[:50]
            lines = [
                f"  {i}. matched_party: {name} | txn_count: {cnt} | total_usd: {usd:,.2f}"
                for i, (name, cnt, usd) in enumerate(shown, start=1)
            ]
            more = (f"\n  … and {len(breakdown) - 50} more matched parties"
                    if len(breakdown) > 50 else "")
            block += ("\nMatched in-scope parties (top 50 by USD paid — these are the TPIs the payments "
                      f"matched to):\n" + "\n".join(lines) + more)
        sample = [" ".join(n.upper().split()).replace("'", "''") for n in names[:12]]
        in_list = ", ".join(f"'{s}'" for s in sample)
        tail = f"\n  /* …and {len(names) - 12} more listed parties */" if len(names) > 12 else ""
        sql = (
            "-- Representative only; authoritative figure uses cosmetic-tolerant token matching (= Output F).\n"
            "SELECT COUNT(*) AS matched_payment_count, SUM(DOCUMENT_CURRENCY_INVOICE_AMT_USD) AS total_usd\n"
            "FROM <AP payment / disbursement table>\n"
            f"WHERE UPPER(TRIM(SUPPLIER_NAME)) IN ({in_list}{tail}\n);"
        )
        return block, sql

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
        except Exception as exc:
            logger.warning("[%s] MAP/GACS remediation retrieval skipped | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, exc)
            return ""

    def _table_for_category_columns(self, category_filter: str) -> str:
        """The dashboard FQN that holds EVERY column used in `category_filter` — but ONLY when exactly
        one dashboard qualifies (unambiguous). e.g. SALE_TYPE_DESCRIPTION -> the OTC dashboard. Returns
        '' when no table or more than one has them (e.g. CATEGORY/REQUEST_TYPE live on both HCC tables),
        so HCC scopes are left to their existing resolution and only single-home columns are steered."""
        cols = {m.upper() for m in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s+IN\s*\(", category_filter or "")}
        if not cols:
            return ""
        hosts: list[str] = []
        for fqn in self._settings.dashboard_tables.values():
            try:
                have = {c.upper() for c, _ in self._get_table_schema(fqn)}
            except Exception:  # noqa: BLE001
                continue
            if have and cols <= have:
                hosts.append(fqn)
        return hosts[0] if len(hosts) == 1 else ""

    def _resolve_scope_category_filter(self, scope_id: str, prefer_hcc: bool = False) -> str:
        """Return a mandatory category predicate scoping this scope's HCC/transactional population.

        HCC scopes with an auditor-provided value list (_HCC_SCOPE_CATEGORY_VALUES) are matched by EXACT
        value in CATEGORY and/or REQUEST_TYPE — only those real values are included, never a substring
        (so 'Sponsorship for the 3rd Party Congress' is Sponsorship, not Congress). Returns e.g.
        "(CATEGORY IN (...) OR REQUEST_TYPE IN (...))". All other scopes use the keyword resolver
        (_SCOPE_CATEGORY_RESOLVER_MAP); first entry whose keyword matches ≥1 real value wins. '' if none.

        `prefer_hcc` (Output F summary ONLY): these activity categories can appear in HCC AND in other
        tables (payment/disbursement/etc.). For the Output F totals we give HCC PRIORITY — resolve from
        the HCC tables first, and consult the other tables ONLY when HCC carries none of the scope's
        values, so the same activity is not double-counted across tables. The calc leaves this off
        (prefer_hcc=False): it resolves table-agnostically and runs its full calculations as before."""
        all_keys = list(self._settings.dashboard_tables)
        if prefer_hcc:
            hcc_keys = [k for k in _HCC_CATEGORY_TABLE_KEYS if k in all_keys]
            other_keys = [k for k in all_keys if k not in _HCC_CATEGORY_TABLE_KEYS]
            table_tiers = [tier for tier in (hcc_keys, other_keys) if tier]
        else:
            table_tiers = [all_keys]

        exact = _HCC_SCOPE_CATEGORY_VALUES.get(scope_id)
        if exact is not None:
            wanted = {v.strip().lower() for v in exact}
            for tier in table_tiers:
                parts: list[str] = []
                for column in _HCC_CATEGORY_COLUMNS:
                    real = self._distinct_values_all_tables(column, tier)
                    matched = sorted({v for v in real if v.strip().lower() in wanted})
                    if matched:
                        quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in matched)
                        parts.append(f"{column} IN ({quoted})")
                if parts:
                    return "(" + " OR ".join(parts) + ")" if len(parts) > 1 else parts[0]

        entries = _SCOPE_CATEGORY_RESOLVER_MAP.get(scope_id)
        if not entries:
            return ""
        for tier in table_tiers:
            for column, keywords in entries:
                real_values = self._distinct_values_all_tables(column, tier)
                matched = sorted({v for v in real_values if any(k in v.lower() for k in keywords)})
                if matched:
                    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in matched)
                    return f"{column} IN ({quoted})"
        return ""


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

        data_summary, table_info = self._pull_data_summary()

        _ = self._remediation_register_block

        scopes = list(guidance_by_scope.items())
        workers = max(1, min(_SCOPE_ANALYSIS_WORKERS, len(scopes)))
        logger.info(
            "[%s] Analysing %d scope(s) with %d concurrent worker(s)",
            AGENT_TRANSACTIONAL_ANALYSIS, len(scopes), workers,
        )

        results: list[RiskOutput] = []

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

        with ThreadPoolExecutor(max_workers=workers) as pool:
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
            except Exception as exc:
                logger.warning("[%s] Scope summary failed | scope=%s | error=%s",
                               AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
                summaries.append(ScopeTxnSummary(scope_id=scope_id))
        for summary in summaries:
            doc_srcs = _SCOPE_DOC_SOURCES.get(summary.scope_id)
            if doc_srcs and not summary.doc_sources:
                summary.doc_sources = list(doc_srcs)
        logger.info("[%s] Built transaction summary for %d scope(s)",
                    AGENT_TRANSACTIONAL_ANALYSIS, len(summaries))
        return summaries

    def build_other_hcc_activities(self) -> list[tuple[str, int, float]]:
        """Output G — HCC activity types (CATEGORY-else-REQUEST_TYPE) that are NOT mapped to any scope
        area, each with its transaction count + total USD. Excludes the values already claimed by a scope
        (_HCC_SCOPE_CATEGORY_VALUES) and the generic 'Event' / 'None' / blank. Engagement + scope-period
        scoped. Returns [(activity_type, txn_count, total_usd), …] ordered by amount DESC."""
        claimed = {v.strip().lower() for vals in _HCC_SCOPE_CATEGORY_VALUES.values() for v in vals}
        excluded = claimed | {"event", "none", ""}

        eff = ("CASE WHEN CATEGORY IS NULL OR trim(CATEGORY) = '' THEN REQUEST_TYPE "
               "WHEN lower(trim(CATEGORY)) RLIKE '^(category|cat)[ -]*[0-9]+$' THEN REQUEST_TYPE "
               "ELSE CATEGORY END")
        usd = ("COALESCE(NULLIF(try_cast(DOCUMENT_CURRENCY_INVOICE_AMT_USD AS DOUBLE), 0), "
               "NULLIF(try_cast(ACTIVITY_APPROVED_AMT_USD AS DOUBLE), 0), "
               "try_cast(TOTAL_ACTIVITY_APPROVED_AMOUNT AS DOUBLE))")

        clauses: list[str] = []
        if self._settings.engagement_filter_clause:
            clauses.append(self._settings.engagement_filter_clause)
        start = (self._settings.scope_period_start or "").strip()
        end = (self._settings.scope_period_end or "").strip()
        dcol = "COALESCE(try_cast(EVENT_START_DATE AS DATE), try_cast(EVENT_END_DATE AS DATE))"
        if start and end:
            clauses.append(f"{dcol} BETWEEN DATE '{start}' AND DATE '{end}'")
        elif start:
            clauses.append(f"{dcol} >= DATE '{start}'")
        elif end:
            clauses.append(f"{dcol} <= DATE '{end}'")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        for table_key in _HCC_CATEGORY_TABLE_KEYS:
            table = self._settings.dashboard_tables.get(table_key)
            if not table:
                continue
            try:
                rows = self._run_sql(
                    f"SELECT {eff} AS activity_type, COUNT(*) AS txn_count, "
                    f"ROUND(SUM({usd}), 2) AS total_usd FROM {table} {where} "
                    f"GROUP BY {eff} ORDER BY total_usd DESC NULLS LAST"
                )
            except Exception as exc:
                logger.warning("[%s] Output G aggregate failed on %s | %s",
                               AGENT_TRANSACTIONAL_ANALYSIS, table_key, exc)
                continue
            out: list[tuple[str, int, float]] = []
            for r in rows:
                name = str(r.get("activity_type") or "").strip()
                if not name or name.lower() in excluded:
                    continue
                cnt = int(self._coerce_number(r.get("txn_count")) or 0)
                amt = float(self._coerce_number(r.get("total_usd")) or 0.0)
                out.append((name, cnt, amt))
            if out:
                logger.info("[%s] Output G — %d other HCC activity type(s) from %s",
                            AGENT_TRANSACTIONAL_ANALYSIS, len(out), table_key)
                return out
        logger.info("[%s] Output G — no unmapped HCC activity types found", AGENT_TRANSACTIONAL_ANALYSIS)
        return []

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

        _AP_KEYS = ("payment", "disbursement")
        ap_keys = [k for k in table_keys if k in _AP_KEYS]
        other_keys = [k for k in table_keys if k not in _AP_KEYS]

        def _summ(table_key: str):
            hints = _TABLE_NAME_HINTS.get(table_key, ("supplier", "vendor", "customer", "name"))
            try:
                return self._name_filtered_table_summary(table_key, hints, names)
            except Exception as exc:
                logger.warning("[%s] %s name-list table summary failed | table=%s | %s",
                               AGENT_TRANSACTIONAL_ANALYSIS, scope_id, table_key, exc)
                return 0, {}, None, {}

        payees: dict[str, list] = {}

        def _merge_payees(src: dict[str, list]) -> None:
            for name, (c, u) in src.items():
                agg = payees.setdefault(name, [0, 0.0])
                agg[0] += c
                agg[1] += u

        best_ap_count, best_ap_by_date, best_ap_payees = 0, {}, {}
        for table_key in ap_keys:
            count, by_date, display, tbl_payees = _summ(table_key)
            if count > 0 and display and display not in sources:
                sources.append(display)
            if count > best_ap_count:
                best_ap_count, best_ap_by_date, best_ap_payees = count, by_date, tbl_payees
        if best_ap_count:
            total_count += best_ap_count
            for day_key, usd in best_ap_by_date.items():
                usd_by_date[day_key] = usd_by_date.get(day_key, 0.0) + usd
            _merge_payees(best_ap_payees)

        for table_key in other_keys:
            count, by_date, display, tbl_payees = _summ(table_key)
            if count > 0:
                total_count += count
                for day_key, usd in by_date.items():
                    usd_by_date[day_key] = usd_by_date.get(day_key, 0.0) + usd
                if display and display not in sources:
                    sources.append(display)
                _merge_payees(tbl_payees)

        payee_breakdown = sorted(
            ((n, c, round(u, 2)) for n, (c, u) in payees.items()),
            key=lambda x: x[2], reverse=True,
        )

        return ScopeTxnSummary(
            scope_id=scope_id,
            txn_count=total_count,
            total_usd=round(sum(usd_by_date.values()), 2),
            usd_by_date=usd_by_date,
            table_sources=sources,
            doc_sources=[doc_label],
            has_data=total_count > 0,
            payee_breakdown=payee_breakdown,
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
            try:
                from src.services.translation import TranslationService

                names = TranslationService(self._settings, llm=self._llm).augment_with_english(names)
            except Exception as exc:
                logger.warning("[%s] %s — name English-augmentation skipped | %s",
                               AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return names
        except Exception as exc:
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
        except Exception as exc:
            hint = " (install xlrd or save as .xlsx)" if suffix == ".xls" else ""
            logger.warning("[%s] Could not read supporting-doc sheet %s%s | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, local_path.name, hint, exc)
            return [], []
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
        except Exception as exc:
            logger.warning("[%s] %s — entity-column mapping failed, using heuristic | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
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
        except Exception as exc:
            logger.warning("[%s] %s — amount-column mapping failed, using heuristic | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
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
        except Exception as exc:
            logger.warning("[%s] %s — doc amount extraction failed | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return 0, {}

    def _name_filtered_table_summary(
        self, table_key: str, name_hints: tuple[str, ...], names: list[str]
    ) -> tuple[int, dict[str, float], str | None, dict[str, list]]:
        """Count + USD-by-date of this audit's rows on `table_key` whose name column matches `names`.
        Returns (count, usd_by_date, friendly_table_name, payees) — payees maps each MATCHED payee to
        [txn_count, total_usd]. (0, {}, None, {}) when the table is missing, has no usable name/USD
        column, or matches nothing. Amounts summed only from a `*_USD` column."""
        table = self._settings.dashboard_tables.get(table_key)
        if not table or not names:
            return 0, {}, None, {}
        schema = self._get_table_schema(table)
        cols = [name for name, _ in schema]
        dtype_by_col = {name: (dtype or "").lower() for name, dtype in schema}

        def _pick(hints: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str | None:
            for hint in hints:
                for col in cols:
                    cl = col.lower()
                    if hint in cl and not any(x in cl for x in exclude):
                        return col
            return None

        def _pick_name(hints: tuple[str, ...]) -> str | None:
            for hint in hints:
                for col in cols:
                    cl = col.lower()
                    if hint in cl and "name" in cl:
                        return col
            return _pick(hints, exclude=("id", "identifier", "code", "number", "group", "flag"))

        name_col = _pick_name(name_hints)
        usd_col = _pick(("usd",))
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
            return 0, {}, None, {}

        def _token_set(value) -> frozenset[str]:
            return frozenset(_normalize_entity(value).split())

        def _significant(ts: frozenset[str]) -> bool:
            return len(ts) >= 2 or any(len(t) >= 5 or (len(t) >= 2 and _CJK_RE.search(t)) for t in ts)

        tpi_sets = {ts for ts in (_token_set(n) for n in names) if ts}

        def _matches(payee_ts: frozenset[str]) -> bool:
            if not payee_ts:
                return False
            for ts in tpi_sets:
                small, big = (ts, payee_ts) if len(ts) <= len(payee_ts) else (payee_ts, ts)
                if not small or not _significant(small):
                    continue
                if small.issubset(big):
                    return True
                inter = small & big
                if len(small) >= 3 and len(inter) >= len(small) - 1 and len(inter) >= 2 \
                        and any(len(t) >= 4 for t in inter):
                    return True
            return False

        if not tpi_sets:
            return 0, {}, None, {}

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
        payee_tot: dict[str, list] = {}
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
                agg = payee_tot.setdefault(str(payee), [0, 0.0])
                agg[0] += c
                agg[1] += u
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
        return count, by_date, display, payee_tot

    def _pricing_discount_summary(self, scope_id: str) -> ScopeTxnSummary | None:
        """Pricing/Discounts scope: ONE row, but the Amount cell carries BOTH tagged values —
        'Sales: USD <sales>; Discount: USD <discount>' — computed deterministically from the Revenue
        table over TRANSACTION_TYPE='Sale' (SALES_AMOUNT and ACTUAL_DISCOUNT_AMOUNT, both USD).
        total_usd stays = sales for any numeric roll-up. None → caller uses the normal path."""
        table = self._settings.dashboard_tables.get("revenue")
        if not table:
            return None
        clause = self._settings.engagement_filter_clause
        sale_pred = "UPPER(try_cast(`TRANSACTION_TYPE` AS STRING)) = 'SALE'"
        where = f"WHERE {clause} AND {sale_pred}" if clause else f"WHERE {sale_pred}"
        sql = (
            "SELECT COUNT(*) AS txn_count, "
            "SUM(try_cast(`SALES_AMOUNT` AS DOUBLE)) AS sales_usd, "
            "SUM(try_cast(`ACTUAL_DISCOUNT_AMOUNT` AS DOUBLE)) AS discount_usd "
            f"FROM {table} {where}"
        )
        try:
            rows = self._run_sql(sql)
        except Exception as exc:
            logger.warning("[%s] pricing/discount summary failed — falling back | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, str(exc)[:160])
            return None
        if not rows:
            return None
        r = rows[0]
        cnt = int(self._coerce_number(r.get("txn_count")) or 0)
        sales = float(self._coerce_number(r.get("sales_usd")) or 0.0)
        disc = float(self._coerce_number(r.get("discount_usd")) or 0.0)
        return ScopeTxnSummary(
            scope_id=scope_id,
            txn_count=cnt,
            total_usd=round(sales, 2),
            usd_by_date={"": round(sales, 2)},
            table_sources=[self._dashboard_source_name(table)] if cnt > 0 else [],
            has_data=cnt > 0,
            amount_display_override=f"Sales: USD {sales:,.2f}; Discount: USD {disc:,.2f}",
        )

    def _product_samples_summary(self, scope_id: str) -> ScopeTxnSummary | None:
        """Product Samples: report sample transactions from a SINGLE dashboard — whichever of OTC or
        Revenue actually HAS the sample rows (not a sum of both). Filters SALE_TYPE_DESCRIPTION ~ 'sample'
        on each table that has the column and returns the FIRST one with data, using that table's own USD
        column (SALES_AMOUNT on Revenue, a *_USD column on OTC). None → caller uses the normal path."""
        clause = self._settings.engagement_filter_clause
        for key in ("otc", "revenue"):
            fqn = self._settings.dashboard_tables.get(key)
            if not fqn:
                continue
            try:
                schema = self._get_table_schema(fqn)
            except Exception:  # noqa: BLE001
                continue
            up = {c.upper(): c for c, _ in schema}
            if "SALE_TYPE_DESCRIPTION" not in up:
                continue
            sale_col = up["SALE_TYPE_DESCRIPTION"]
            usd_col = up.get("SALES_AMOUNT") or next((c for c, _ in schema if "usd" in c.lower()), None)
            date_col = next(
                (c for c, d in schema
                 if ("date" in (d or "").lower() or "timestamp" in (d or "").lower())
                 and not any(x in c.lower() for x in ("count", "distinct", "num", "qty"))),
                None,
            )
            date_expr = f"try_cast(`{date_col}` AS DATE)" if date_col else "CAST(NULL AS DATE)"
            amt_expr = (f"SUM(COALESCE(try_cast(`{usd_col}` AS DOUBLE), 0))" if usd_col
                        else "CAST(0 AS DOUBLE)")
            where = f"WHERE {clause} AND " if clause else "WHERE "
            sql = (
                f"SELECT {date_expr} AS txn_date, COUNT(*) AS txn_count, {amt_expr} AS total_usd "
                f"FROM {fqn} {where}LOWER(try_cast(`{sale_col}` AS STRING)) LIKE '%sample%' "
                f"GROUP BY {date_expr}"
            )
            try:
                rows = self._run_sql(sql)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] product-samples %s query failed | %s",
                               AGENT_TRANSACTIONAL_ANALYSIS, key, str(exc)[:140])
                continue
            usd_by_date: dict[str, float] = {}
            count = 0
            for r in rows:
                c = int(self._coerce_number(r.get("txn_count")) or 0)
                u = float(self._coerce_number(r.get("total_usd")) or 0.0)
                day = r.get("txn_date")
                dk = "" if day in (None, "") else str(day)[:10]
                count += c
                usd_by_date[dk] = usd_by_date.get(dk, 0.0) + u
            if count > 0:   # first dashboard that actually has sample rows wins — no summation
                return ScopeTxnSummary(
                    scope_id=scope_id,
                    txn_count=count,
                    total_usd=round(sum(usd_by_date.values()), 2),
                    usd_by_date=usd_by_date,
                    table_sources=[self._dashboard_source_name(fqn)],
                    has_data=True,
                )
        return None

    def _scope_summary_for(self, scope_id: str, guidance: dict[str, str], table_info: str) -> ScopeTxnSummary:
        """Generate + run ONE count/USD-by-date summary query for a scope; aggregate into a summary."""
        resolved = _resolve_scope_id(scope_id.replace("_", " ")) or scope_id.strip().upper()
        cfg = _NAME_LIST_SCOPES.get(resolved)
        if cfg:
            keywords, table_keys, doc_label = cfg
            names = self._name_list_for_scope(resolved, keywords)
            summary = self._name_list_summary(resolved, names, table_keys, doc_label)
            doc_count, doc_by_date = self._supporting_doc_amount_summary(resolved, keywords)
            if doc_count:
                summary.doc_txn_count = doc_count
                summary.doc_usd_by_date = doc_by_date
                summary.doc_total_usd = round(sum(doc_by_date.values()), 2)
                summary.has_data = summary.has_data or doc_count > 0
            if summary.has_data or doc_count or resolved not in _SCOPE_FALLBACK_DASHBOARD:
                return summary
            logger.info("[%s] %s name-list empty — falling through to AP dashboard fallback",
                        AGENT_TRANSACTIONAL_ANALYSIS, resolved)

        if resolved == "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS":
            pd_summary = self._pricing_discount_summary(scope_id)
            if pd_summary is not None:
                return pd_summary

        if resolved == "PRODUCT_SAMPLES":
            ps_summary = self._product_samples_summary(scope_id)
            if ps_summary is not None:
                return ps_summary

        category_filter = self._resolve_scope_category_filter(scope_id, prefer_hcc=True)

        fallback_keys = _SCOPE_FALLBACK_DASHBOARD.get(resolved)
        dashboard_hint = ""
        if not category_filter:
            if not fallback_keys:
                return ScopeTxnSummary(scope_id=scope_id)
            fqns = [self._settings.dashboard_tables[k] for k in fallback_keys
                    if k in self._settings.dashboard_tables]
            if not fqns:
                return ScopeTxnSummary(scope_id=scope_id)
            if len(fqns) > 1:
                dashboard_hint = (
                    "\n\nPRIMARY DASHBOARD(S) FOR THIS SCOPE — use ONLY these (the Payment + Disbursement "
                    f"AP dashboards): {', '.join(fqns)}. They MIRROR the same AP payments, so UNION ALL "
                    "both and DE-DUPLICATE (one row per COMPANY_CODE, INVOICE_NUMBER, "
                    "try_cast(DOCUMENT_CURRENCY_INVOICE_AMT_USD AS DOUBLE), INVOICE_POSTING_DATE) before "
                    "counting/summing — NEVER sum the two tables (that double-counts). Restrict to THIS "
                    "scope's relevant transactions using the Scope Description above."
                )
            else:
                dashboard_hint = (
                    f"\n\nPRIMARY DASHBOARD FOR THIS SCOPE — use ONLY this table: {fqns[0]}. Restrict to "
                    "THIS scope's relevant transactions using the Scope Description above."
                )
        else:
            # category_filter present: if its column lives on exactly ONE dashboard (e.g.
            # SALE_TYPE_DESCRIPTION for Product Samples -> OTC, not Revenue), force the summary onto that
            # table. Otherwise the LLM may query a different dashboard that lacks the column → the whole
            # query fails with UNRESOLVED_COLUMN and the scope silently shows nothing.
            host = self._table_for_category_columns(category_filter)
            if host:
                dashboard_hint = (
                    f"\n\nPRIMARY DASHBOARD FOR THIS SCOPE — use ONLY this table: {host}. It is the table "
                    "that holds the SCOPE CATEGORY FILTER column(s); do NOT query any other table."
                )

        user_msg = SCOPE_SUMMARY_SQL_USER_TEMPLATE.format(
            scope_area=scope_id,
            engagement_filter=self._engagement_filter,
            scope_period_filter=self._scope_period_filter,
            category_filter=category_filter
                or "(none — compute the scope-relevant population on the mapped dashboard per the guidance below)",
            scope_description=(guidance.get("scope_description") or "Not available for this scope area.") + dashboard_hint,
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
        # Same permanent apostrophe/punctuation fix as the calc path: the Output F summary SQL also
        # filters REQUEST_TYPE / CATEGORY, so normalise those predicates to alphanumeric-only on both
        # sides — otherwise a typographic apostrophe in the data (HCP’s) silently returns 0 here too.
        sql, _changed = self._normalize_category_predicates(sql)

        # Column guard (same as the calc path): the summary SQL can reference a column the LLM invented or
        # that does not exist on the table it chose (e.g. REQUEST_TYPE on the T&E dashboard for Market
        # Access), which fails the WHOLE query and silently drops the scope. Validate against the shown
        # schema and repair (drop the offending predicate, keep the aggregate) before running.
        cols_by_table = self._known_columns_by_table(table_info)
        bad = self._unknown_columns(sql, cols_by_table)
        if bad:
            pre_err = (
                "Pre-execution validation: column(s) " + ", ".join(bad) + " are NOT in the table schema "
                "and WILL fail with UNRESOLVED_COLUMN. Remove the predicate/metric that uses each one (do "
                "NOT invent a substitute). Keep exactly the output columns txn_date, txn_count = COUNT(*), "
                "total_usd, and keep the engagement + scope-period filters."
            )
            repaired = self._repair_calc_sql(scope_id, "scope transaction summary", sql, pre_err, table_info)
            if repaired and not self._unknown_columns(repaired, cols_by_table):
                logger.info("[%s] Output F summary column-repaired | scope=%s | removed=%s",
                            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, ", ".join(bad))
                sql = repaired

        try:
            rows = self._run_sql(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Output F summary SQL failed | scope=%s | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, str(exc)[:160])
            return ScopeTxnSummary(scope_id=scope_id)
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
        calc_results, calc_sqls = self._run_scope_calculations(scope_id, guidance, table_info)
        data_for_judgment = calc_results or fallback_summary

        finding = self._analyze_with_ai(scope_id, guidance, data_for_judgment, table_info)
        if not finding:
            finding = {
                "risk_type": "No Risk Identified",
                "risk_title": "No transactional risk identified",
                "risk_description": "No material transactional risk was identified for this scope area.",
            }

        expected_size = self._parse_sample_size(guidance.get("expected_sample_size", ""))
        target_samples = expected_size

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


    @staticmethod
    def _known_columns_by_table(table_info: str) -> dict[str, set[str]]:
        """Parse the table_info the LLM was shown into {base_table_name_lower: {UPPER column names}}.
        _table_info_line emits `Table: <catalog.schema.name>` then `Columns: `c1` (t), `c2` (t) …`, so
        we attribute each backtick-quoted column to the most recent `Table:` header. Empty dict => a
        parse miss, which the caller treats as "skip validation" rather than flagging good columns."""
        by_table: dict[str, set[str]] = {}
        current: str | None = None
        for line in (table_info or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("Table:"):
                raw = stripped[len("Table:"):].strip().split()[0] if stripped[len("Table:"):].strip() else ""
                current = raw.split(".")[-1].strip("`").lower() or None
                if current:
                    by_table.setdefault(current, set())
            elif current:
                for col in re.findall(r"`([A-Za-z0-9_]+)`", line):
                    by_table[current].add(col.upper())
        return by_table

    @classmethod
    def _unknown_columns(cls, sql: str, by_table: dict[str, set[str]]) -> list[str]:
        """UPPER_SNAKE tokens in `sql` that are NOT a real column of the table(s) the query reads, NOT a
        SQL keyword, NOT a function call, and NOT an alias the query itself defines — i.e. columns the
        LLM invented (or a real column used on a table that lacks it). Order-preserving + unique.

        Validation is scoped to the tables actually referenced in FROM/JOIN, so a column that exists on
        another dashboard is still flagged when used on a table without it (e.g. GOVERNMENT_VENDORS on
        payment/disbursement). Returns [] when nothing can be resolved, so a parse miss never over-flags."""
        if not by_table or not sql:
            return []
        stripped = _SQL_STRING_LITERAL_RE.sub(" ", sql)

        referenced = {
            m.split(".")[-1].strip("`").lower()
            for m in _FROM_JOIN_RE.findall(stripped)
        }
        valid: set[str] = set()
        for tbl in referenced:
            valid |= by_table.get(tbl, set())
        if not valid:
            for cols in by_table.values():
                valid |= cols

        defined = {m.upper() for m in _AS_ALIAS_RE.findall(stripped)}
        defined |= {m.upper() for m in _CTE_NAME_RE.findall(stripped)}

        unknown: list[str] = []
        seen: set[str] = set()
        for m in _UPPER_IDENT_RE.finditer(stripped):
            tok = m.group(0)
            if tok in seen or tok in valid or tok in defined or tok in _SQL_RESERVED_TOKENS:
                continue
            rest = stripped[m.end():]
            if rest.lstrip().startswith("("):
                continue
            seen.add(tok)
            unknown.append(tok)
        return unknown

    @classmethod
    def _autocorrect_columns(cls, sql: str, by_table: dict[str, set[str]]) -> tuple[str, list[str]]:
        """Deterministically fix near-miss column TYPOS before execution: any invented UPPER_SNAKE
        identifier that CLOSELY matches a real column (e.g. EQUIPEMENT_SCORE -> EQUIPMENT_SCORE) is
        replaced in place. Genuinely invented columns with no close real match (e.g. ADVANCE_PAYMENT)
        are left untouched for the normal guard/repair to drop. Returns (sql, ['TYPO->REAL', ...])."""
        import difflib
        unknown = cls._unknown_columns(sql, by_table)
        if not unknown:
            return sql, []
        valid = sorted({c for cols in by_table.values() for c in cols})
        if not valid:
            return sql, []
        fixes: list[str] = []
        for tok in unknown:
            match = difflib.get_close_matches(tok, valid, n=1, cutoff=0.85)
            if match and match[0] != tok:
                sql = re.sub(rf"\b{re.escape(tok)}\b", match[0], sql)
                fixes.append(f"{tok}->{match[0]}")
        return sql, fixes

    def _run_scope_calculations(
        self, scope_id: str, guidance: dict[str, str], table_info: str
    ) -> tuple[str, list[str]]:
        """Generate SQL from the scope's Data Calculations, execute it, and return the computed
        results as a text block together with the list of executed SQL queries. Returns ("", [])
        if nothing relevant could be computed."""
        queries = self._generate_calc_sql(scope_id, guidance, table_info)

        tpi_block, tpi_sql = self._tpi_payment_calc_block(scope_id)

        if not queries:
            if tpi_block:
                return tpi_block, ([tpi_sql] if tpi_sql else [])
            logger.info("[%s] No scope calculations generated — falling back to generic summary | scope=%s",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id)
            return "", []

        cols_by_table = self._known_columns_by_table(table_info)
        blocks: list[str] = []
        executed_sqls: list[str] = []
        if tpi_block:
            blocks.append(tpi_block)
            if tpi_sql:
                executed_sqls.append(tpi_sql)
        n_failed = 0
        n_with_rows = 0
        n_no_rows = 0
        for q in queries:
            purpose = self._clean_calc_purpose(q.get("purpose") or "calculation")
            sql = (q.get("sql") or "").strip()
            if not sql:
                continue
            sql = self._apply_limit(sql, 50)   # behind-the-scenes display cap (not shown in the evidence line)

            sql, typo_fixes = self._autocorrect_columns(sql, cols_by_table)
            if typo_fixes:
                logger.info("[%s] Calc query column typos auto-corrected | scope=%s | purpose=%s | %s",
                            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose, ", ".join(typo_fixes))

            bad_cols = self._unknown_columns(sql, cols_by_table)
            if bad_cols:
                pre_err = (
                    "Pre-execution validation: the following column(s) are NOT in the table schema and "
                    "WILL fail with UNRESOLVED_COLUMN. Remove the metric that uses each one, or replace "
                    "it with a real column from the AVAILABLE TABLES AND COLUMNS block — do NOT invent a "
                    f"substitute: {', '.join(bad_cols)}"
                )
                repaired = self._repair_calc_sql(scope_id, purpose, sql, pre_err, table_info)
                repaired = self._apply_limit(repaired, 50) if repaired else ""
                if not repaired or self._unknown_columns(repaired, cols_by_table):
                    n_failed += 1
                    logger.warning(
                        "[%s] Calc query excluded (hallucinated column, not repairable) | scope=%s | "
                        "purpose=%s | columns=%s",
                        AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose, ", ".join(bad_cols),
                    )
                    continue
                logger.info(
                    "[%s] Calc query column-repaired before execution | scope=%s | purpose=%s | "
                    "removed/replaced=%s",
                    AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose, ", ".join(bad_cols),
                )
                sql = repaired

            executed_sql = sql
            table_disp = self._workflow_display(self._extract_table_from_sql(sql)) or "Not Available"
            try:
                rows = self._run_sql(sql)
            except Exception as exc:
                repaired = self._repair_calc_sql(scope_id, purpose, sql, str(exc), table_info)
                if repaired:
                    executed_sql = self._apply_limit(repaired, 50)
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
            summary = self._summarize_full_population(executed_sql, cols) or self._summarize_calc_rows(rows, cols)
            blocks.append(
                f"Calculation: {purpose}\nTable: {table_disp}\n"
                f"{summary}\n{self._format_table_rows(rows, cols)}"
            )
            executed_sqls.append(executed_sql)

        logger.info(
            "[%s] Scope calculations executed | scope=%s | queries=%d | with_rows=%d | no_rows=%d | failed_excluded=%d",
            AGENT_TRANSACTIONAL_ANALYSIS, scope_id, len(queries), n_with_rows, n_no_rows, n_failed,
        )
        if n_with_rows == 0:
            if tpi_block:
                logger.info(
                    "[%s] Generic calc queries empty; retaining deterministic in-scope-payee block | scope=%s",
                    AGENT_TRANSACTIONAL_ANALYSIS, scope_id,
                )
                return _CALC_BLOCK_SEPARATOR.join(blocks), executed_sqls
            logger.info(
                "[%s] All calc queries returned no rows — falling back to the generic data summary | scope=%s",
                AGENT_TRANSACTIONAL_ANALYSIS, scope_id,
            )
            return "", executed_sqls
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
            if not raw or "select" not in raw.lower():
                return ""
            return raw
        except Exception as exc:
            logger.warning("[%s] Calc SQL repair failed | scope=%s | purpose=%s | error=%s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, purpose, exc)
            return ""

    def _generate_calc_sql(self, scope_id: str, guidance: dict[str, str], table_info: str) -> list[dict]:
        """Ask the LLM to turn the scope's Data Calculations into concrete SQL queries."""
        category_filter = self._resolve_scope_category_filter(scope_id)
        user_msg = CALC_SQL_USER_TEMPLATE.format(
            scope_area=scope_id,
            engagement_filter=self._engagement_filter,
            scope_period_filter=self._scope_period_filter,
            category_filter=category_filter
                or "(none — infer the activity filter from the real CATEGORY values listed in the table info)",
            data_calculations=guidance.get("data_calculations")
                or "Not available — use your own FCPA audit intelligence to decide what to compute.",
            red_flags=guidance.get("red_flags") or "Not available.",
            table_info=table_info or "Table schema not available.",
        )
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
            queries = [q for q in queries if isinstance(q, dict) and q.get("sql")]
            if scope_id in _HCC_SCOPE_CATEGORY_VALUES and category_filter:
                names = self._settings.all_dashboard_table_names
                hcc_fragments = [
                    str(names.get(k, "")).split(".")[-1].strip("`").lower()
                    for k in _HCC_CATEGORY_TABLE_KEYS if names.get(k)
                ]
                for q in queries:
                    low = q["sql"].lower()
                    if any(frag and frag in low for frag in hcc_fragments):
                        q["sql"] = self._enforce_category_filter(q["sql"], category_filter)
            # PERMANENT apostrophe/punctuation fix: rewrite every REQUEST_TYPE / CATEGORY filter to
            # compare the ALPHANUMERIC-ONLY UPPER form of both sides, so a typographic apostrophe in the
            # stored value (HCP’s) vs the straight one the LLM writes (HCP''s) can no longer miss. Static,
            # no DB lookup, no LLM — applied to every HCC-category scope's queries.
            if scope_id in _HCC_SCOPE_CATEGORY_VALUES or scope_id in _SCOPE_CATEGORY_RESOLVER_MAP:
                for q in queries:
                    q["sql"], changed = self._normalize_category_predicates(q["sql"])
                    if changed:
                        logger.info("[%s] Calc category predicate made apostrophe/punctuation-insensitive "
                                    "| scope=%s", AGENT_TRANSACTIONAL_ANALYSIS, scope_id)
            return queries
        except Exception as exc:
            logger.warning("[%s] Calc SQL generation failed | scope=%s | error=%s",
                           AGENT_TRANSACTIONAL_ANALYSIS, scope_id, exc)
            return []


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
            scope_period_filter=self._scope_period_filter,
            scope_description=guidance.get("scope_description") or "Not available for this scope area.",
            red_flags=guidance.get("red_flags") or "Not available for this scope area.",
            data_calculations=guidance.get("data_calculations") or "Not available for this scope area.",
            sampling_rationale=guidance.get("sampling_rationale") or "Not available for this scope area.",
            expected_sample_size=guidance.get("expected_sample_size") or "Not available for this scope area.",
            table_info=table_info or "Table schema not available.",
            data_summary=data_summary or "No transactional data available.",
        )
        if self._tpi_inventory_block:
            user_msg = f"{user_msg}\n\n{self._tpi_inventory_block}"
        if self._gl_account_map_block:
            user_msg = f"{user_msg}\n\n{self._gl_account_map_block}"
        if self._remediation_register_block:
            user_msg = f"{user_msg}\n\n{self._remediation_register_block}"
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


    def _pull_samples(self, sample_query: str, limit: int) -> str:
        """Execute the LLM-generated sample query and format rows as bullet points.
        The projection is forced to SELECT * so each sample includes the FULL transaction
        record (all columns), and the row count is forced to `limit`."""
        if not sample_query or not sample_query.strip():
            return ""
        query = self._force_select_star(sample_query)
        query = self._apply_limit(query, limit)
        tbl = self._extract_table_from_sql(query)

        def _cols_of(fqn: str) -> set[str]:
            try:
                return {c.upper() for c, _ in self._get_table_schema(fqn)}
            except Exception:
                return set()

        if tbl:
            base = tbl.split(".")[-1].strip("`").lower()
            by_table = {base: _cols_of(tbl)}
            query, fixes = self._autocorrect_columns(query, by_table)
            if fixes:
                logger.info("[%s] Sample query column typos auto-corrected | %s",
                            AGENT_TRANSACTIONAL_ANALYSIS, ", ".join(fixes))
            bad = self._unknown_columns(query, by_table)
            if bad:
                for sib_key in ("disbursement", "payment"):
                    sib = self._settings.dashboard_tables.get(sib_key)
                    if not sib or sib == tbl:
                        continue
                    sib_cols = _cols_of(sib)
                    if sib_cols and all(b in sib_cols for b in bad):
                        query = query.replace(tbl, sib)
                        tbl, by_table = sib, {sib.split(".")[-1].strip("`").lower(): sib_cols}
                        logger.info("[%s] Sample query retargeted %s -> %s (columns %s not on original)",
                                    AGENT_TRANSACTIONAL_ANALYSIS, base, sib_key, ", ".join(bad))
                        bad = self._unknown_columns(query, by_table)
                        break
            if bad:
                logger.info("[%s] Sample query references column(s) not on %s: %s — using "
                            "engagement-scoped sample instead", AGENT_TRANSACTIONAL_ANALYSIS, base,
                            ", ".join(bad))
                return self._engagement_scoped_sample(tbl, limit)

        try:
            rows = self._run_sql(query)
            if not rows:
                return "No sample rows matched the query."
            return self._format_samples(rows)
        except Exception as exc:
            logger.warning("[%s] Sample query failed | error=%s | query=%s",
                           AGENT_TRANSACTIONAL_ANALYSIS, exc, query[:200])
            if tbl:
                fb = self._engagement_scoped_sample(tbl, limit)
                if fb:
                    return fb
            return f"Sample query failed: {exc}"

    def _engagement_scoped_sample(self, tbl: str, limit: int) -> str:
        """Representative rows for a scope when its sample query can't run (a referenced column is not on
        the table and no sibling carries it). Labelled honestly so it is clear these are NOT the original
        filtered population. '' on failure."""
        if not tbl:
            return ""
        clause = self._settings.engagement_filter_clause
        where = f"WHERE {clause} " if clause else ""
        try:
            rows = self._run_sql(f"SELECT * FROM {tbl} {where}LIMIT {limit}")
        except Exception as exc:
            logger.warning("[%s] Engagement-scoped sample failed | %s", AGENT_TRANSACTIONAL_ANALYSIS, exc)
            return ""
        if not rows:
            return "No sample rows matched the query."
        return ("(Sample query referenced an unavailable column; showing engagement-scoped sample rows "
                "instead.)\n" + self._format_samples(rows))

    @staticmethod
    def _parse_sample_size(text: str) -> int:
        """Extract the expected sample size from CPR column K free text.
        Handles single numbers ("25"), ranges ("3-5 invoices" → upper bound 5), and
        phrasings like "Minimum 20". Ignores percentages and falls back to
        _DEFAULT_SAMPLE_SIZE when no usable absolute count is present."""
        if not text:
            return _DEFAULT_SAMPLE_SIZE
        cleaned = re.sub(r"\d+\s*%", " ", text)
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
    def _clean_calc_purpose(purpose: str) -> str:
        """Strip the row-count claim from a calc purpose shown in Calculation Evidence. The LLM writes
        "Top 50 vendors …", but the actual result may have more or fewer rows (the LIMIT 50 is just a
        behind-the-scenes display cap). Remove the count so the evidence line never states an exact
        number: "Top 50 vendors by spend" -> "Top vendors by spend"."""
        s = (purpose or "calculation").strip()
        s = re.sub(r"\b(top|first|bottom)[\s-]+\d+\b", r"\1", s, flags=re.IGNORECASE)
        s = re.sub(r"\blimit(?:ed)?\s+(?:to\s+)?\d+\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s{2,}", " ", s).strip(" -–—,;")
        return s or "calculation"

    @staticmethod
    def _extract_table_from_sql(sql: str) -> str:
        """Return the REAL dashboard table name(s) referenced in FROM/JOIN clauses, comma-joined (unique,
        in order). Captures BOTH tables of a payment+disbursement UNION so the source reflects every table
        used. Excludes CTE aliases (e.g. a `WITH te AS (...)` name read as `FROM te`) — those are query
        scaffolding, NOT data sources, and must never surface in the Calculation Evidence 'Table:' line.
        Real dashboard tables are fully qualified (catalog.schema.table); CTE aliases are bare names."""
        if not sql:
            return ""
        cte_names = {m.lower() for m in _CTE_NAME_RE.findall(sql)}
        real: list[str] = []
        fallback: list[str] = []
        for m in re.finditer(r"\b(?:from|join)\s+([A-Za-z0-9_.`]+)", sql, re.IGNORECASE):
            t = m.group(1).strip().replace("`", "")
            low = t.lower()
            if not t or low in ("(", "select") or low in cte_names:
                continue
            if t not in fallback:
                fallback.append(t)
            if "." in t and t not in real:   # fully-qualified -> an actual dashboard table
                real.append(t)
        return ", ".join(real or fallback)


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
        norm_filter = re.sub(r"\s+", " ", category_filter).strip().lower()
        if norm_filter in re.sub(r"\s+", " ", sql).lower():
            return sql
        group_by = re.search(r"\bGROUP\s+BY\b", sql, re.IGNORECASE)
        if group_by:
            pos = group_by.start()
            return sql[:pos].rstrip() + f"\n  AND {category_filter}\n" + sql[pos:]
        return sql.rstrip() + f"\n  AND {category_filter}"

    @staticmethod
    def _normalize_category_predicates(sql: str) -> tuple[str, bool]:
        """Make every standalone REQUEST_TYPE / CATEGORY equality or IN predicate apostrophe-, spacing-
        and punctuation-INSENSITIVE by comparing the ALPHANUMERIC-ONLY UPPER form of BOTH the column and
        the literal(s). Permanently fixes the class where the stored value uses a typographic apostrophe
        (HCP’s) but the LLM wrote a straight one (HCP''s) → 0 rows. Static: no DB lookup, no LLM.
        Returns (sql, changed). Only the exact columns REQUEST_TYPE / CATEGORY are touched (word-bounded,
        so CATEGORY_OF_HCP_… and CATEGORY_HCP_NON_HCP are left alone)."""
        changed = False

        def _alnum(v: str) -> str:
            return re.sub(r"[^A-Z0-9]", "", v.upper())

        def _col(c: str) -> str:
            return f"REGEXP_REPLACE(UPPER({c}), '[^A-Z0-9]', '')"

        def _in(m: re.Match) -> str:
            nonlocal changed
            col, inner = m.group(1), m.group(2)
            lits = [_alnum(l.replace("''", "'")) for l in re.findall(r"'((?:[^']|'')*)'", inner)]
            lits = [x for x in lits if x]
            if not lits:
                return m.group(0)
            changed = True
            return f"{_col(col)} IN ({', '.join(chr(39) + x + chr(39) for x in lits)})"

        def _eq(m: re.Match) -> str:
            nonlocal changed
            col, lit = m.group(1), m.group(2)
            val = _alnum(lit.replace("''", "'"))
            if not val:
                return m.group(0)
            changed = True
            return f"{_col(col)} = '{val}'"

        sql = re.sub(r"\b(REQUEST_TYPE|CATEGORY)\b\s+IN\s*\(([^)]*)\)", _in, sql, flags=re.IGNORECASE)
        sql = re.sub(r"\b(REQUEST_TYPE|CATEGORY)\b\s*=\s*'((?:[^']|'')*)'", _eq, sql, flags=re.IGNORECASE)
        return sql, changed

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

    def _summarize_full_population(self, executed_sql: str, cols: list[str]) -> str | None:
        """FULL-POPULATION roll-up: COUNT(*) + SUM(measure) over the ENTIRE filtered population, NOT just
        the top-50 rows displayed. Wraps the executed query (minus its trailing LIMIT) as a subquery and
        aggregates it in SQL. Returns None on any problem so the caller can fall back to the row roll-up."""
        inner = re.sub(r"(?is)\blimit\b\s+\d+\s*(?:offset\s+\d+\s*)?;?\s*$", "",
                       executed_sql.rstrip().rstrip(";")).rstrip()
        if not inner:
            return None
        measures = [c for c in cols if not _NON_MEASURE_COL_RE.search(c)]
        sums = ", ".join(f"SUM(try_cast(`{c}` AS DOUBLE)) AS `{c}`" for c in measures)
        agg_sql = f"SELECT COUNT(*) AS _row_count{(', ' + sums) if sums else ''} FROM (\n{inner}\n) _pop"
        try:
            res = self._run_sql(agg_sql)
        except Exception as exc:
            logger.warning("[%s] full-population summary failed — falling back to row roll-up | %s",
                           AGENT_TRANSACTIONAL_ANALYSIS, str(exc)[:160])
            return None
        if not res:
            return None
        r = res[0]
        n = int(self._coerce_number(r.get("_row_count")) or 0)
        totals = []
        for c in measures:
            v = self._coerce_number(r.get(c))
            if v is not None:
                totals.append(f"{c}={v:,.2f}")
        summary = f"Summary (full population): {n} row(s)"
        if totals:
            summary += "; " + "; ".join(totals)
        return summary

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
        return build_engine(
            settings,
            schema=settings.databricks_schema_name,
            pool_size=_SCOPE_ANALYSIS_WORKERS + 2,
            max_overflow=_SCOPE_ANALYSIS_WORKERS,
            pool_pre_ping=True,
        )
