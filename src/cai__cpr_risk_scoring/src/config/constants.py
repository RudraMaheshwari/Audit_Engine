# ── Databricks catalog / schema ───────────────────────────────────────────────
DATABRICKS_CATALOG = "nbod_gaagd"
DATABRICKS_SCHEMA  = "cpr_risk_analysis"

_VOL = f"/Volumes/{DATABRICKS_CATALOG}/{DATABRICKS_SCHEMA}"

# ── Volume paths ──────────────────────────────────────────────────────────────
HCBI_PDF_FILENAME                = "TV-POL-02257_v3.0.pdf"
HCC_PDF_FILENAME                 = "TV-POL-02312_v2.0.pdf"

# Global scope-area policy documents (formerly TPI Due Diligence "local_policies_tpi").

SUPPORTING_DOCS_VOLUME_PATH      = f"{_VOL}/auditors_supporting_docs"


# ── Transactional dashboard schema (raw GLD tables live here, separate schema) ─

# ── Transactional dashboard tables (the 7 in-use GLD dashboards) ──────────────
GLD_DISBURSEMENT_TABLE   = "gld__disbursement_dashboard__cpr"
GLD_PAYMENT_TABLE        = "gld__payment_dashboard__cpr"
GLD_HCC_TABLE            = "gld__user_input__hcc_dashboard__cpr"
GLD_OTC_TABLE            = "gld__user_input__otc_dashboard__cpr"
GLD_PRICING_TABLE        = "gld__user_input__pricing_dashboard__cpr"
GLD_REVENUE_TABLE        = "gld__user_input__revenue_dashboard__cpr"
GLD_TRAVEL_EXPENSE_TABLE = "gld__user_input__travel_and_expense_dashboard__cpr"

TRANSACTIONAL_DASHBOARD_TABLES = {
    "disbursement":     GLD_DISBURSEMENT_TABLE,
    "payment":          GLD_PAYMENT_TABLE,
    "hcc":              GLD_HCC_TABLE,
    "otc":              GLD_OTC_TABLE,
    "pricing":          GLD_PRICING_TABLE,
    "revenue":          GLD_REVENUE_TABLE,
    "travel_expense":   GLD_TRAVEL_EXPENSE_TABLE,
}

DASHBOARD_TABLE_DESCRIPTIONS = {
    "disbursement":   "disbursement transactions (GL account, amount, cost centre, dates)",
    "payment":        "vendor/TPI payment transactions (supplier, GL account, invoice amount, dates, PO)",
    "hcc":            "HCC activity dashboard (user input)",
    "otc":            "OTC dashboard (user input)",
    "pricing":        "pricing dashboard (discounts, price changes, margins)",
    "revenue":        "revenue / billing data (customer, billing values, transaction type)",
    "travel_expense": "travel and expense data",
    "hcc_full":                "HCC activity dashboard (full analysis: GL account, HCP, event, FMV, approval flags)",
    "hcc_attendee":            "HCC event attendee-level data (attendees per event / activity)",
    "travel_expense_attendee": "T&E event attendee-level data (attendees per report / event)",
}

# Friendly workflow names shown in transactional source details instead of raw Gold-layer table
# names (keyed by the table's base name, i.e. the segment after catalog.schema).
DASHBOARD_WORKFLOW_NAMES = {
    "gld__disbursement_dashboard__cpr":                      "Disbursement",
    "gld__hcc_dashboar__cpr":                                "HCC",
    "gld__hcc_attendee_dashboard__cpr":                      "HCC Attendee",
    "gld__user_input__hcc_dashboard__cpr":                   "HCC (UI)",
    "gld__payment_dashboard__cpr":                           "Payment",
    "gld__user_input__otc_dashboard__cpr":                  "OTC",
    "gld__user_input__pricing_dashboard__cpr":              "Pricing",
    "gld__user_input__revenue_dashboard__cpr":              "Revenue",
    "gld__user_input__travel_and_expense_dashboard__cpr":  "T&E",
    "gld__travel_and_expense_attendee_dashboard__cpr":      "T&E Attendee",
}

# ── Delta tables (legacy — superseded by TRANSACTIONAL_DASHBOARD_TABLES) ───────

IDR_TABLE      = "idr_sam_1"


# ── Vector index names (v2 — new names so old unused indexes are not touched) ─
HCBI_INDEX_NAME                 = "hcbi_policy_index"
# Global scope-area policy index (formerly the "tpi_due_diligence_index").
POLICY_INDEX_NAME               = "global_scope_area_policies"
SUPPORTING_DOCS_INDEX_NAME      = "auditor_supporting_docs_index"

# ── Knowledge-base indexes (scoped per auditor via AUDITOR_ID metadata) ───────

# ── Knowledge-base volume paths (parent folders; one subfolder per auditor) ───

# ── Per-auditor scoping metadata key ──────────────────────────────────────────

# ── Scope area policy file (CPR Excel) ────────────────────────────────────────

# ── Auditors supporting documents volume ──────────────────────────────────────
AUDITORS_SUPPORTING_DOCS_VOLUME_PATH = f"{_VOL}/auditors_supporting_docs"

# ── Azure OpenAI model deployment ─────────────────────────────────────────────
GPT5_DEPLOYMENT    = "gpt-5"

# ── Embedding model ───────────────────────────────────────────────────────────
EMBEDDING_DEPLOYMENT = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 3072

# ── Chunking defaults ─────────────────────────────────────────────────────────

# ── Retrieval defaults ────────────────────────────────────────────────────────
HCBI_SCOPE_ID       = "global"


# ── Scope identification ───────────────────────────────────────────────────────
SCOPE_CONFIDENCE_MIN = 0.60
SCOPE_UNCLEAR_LABEL  = "Not Applicable"
# Applied to a row when the per-question pipeline failed to produce a classification, so the item
# surfaces for a human instead of being silently dropped. Excluded from risk aggregation.
MANUAL_REVIEW_LABEL  = "Manual Review Required"

# Confidence tiers for scope routing (see AuditSupervisor._run_scope_identification):
#   >= HIGH      → use identified scope(s) as-is (tight, precise retrieval)
#   MODERATE..HIGH → use identified scope(s) incl. any additional (broader net)
#   <  MODERATE  → no reliable scope → unfiltered semantic search across all 48 areas
SCOPE_CONFIDENCE_HIGH     = 0.70
SCOPE_CONFIDENCE_MODERATE = 0.50
# Sentinel injected into the message when no reliable scope was identified; instructs the
# policy retrieval to search all scope areas by semantic relevance (no scope_id filter).
SCOPE_UNFILTERED_LABEL    = "ANY_SCOPE"

# ── Risk labels ────────────────────────────────────────────────────────────────
RISK_POTENTIAL      = "Potential Risk"
RISK_FURTHER_REVIEW = "Further Review Required"
RISK_NO_RISK        = "No Risk Identified"


# ── Risk Matrix (approved Impact x Likelihood model — never rescaled/averaged) ─
RISK_CATEGORY_HIGH   = "High Risk"
RISK_CATEGORY_MEDIUM = "Medium Risk"
RISK_CATEGORY_LOW    = "Low Risk"
RISK_CATEGORY_NONE   = "No Risk"
# Further Review Required carries NO severity band — it is an open item, not a scored risk.
# Distinct from "No Risk" (a decided no-finding) so the two are never conflated.
RISK_CATEGORY_NOT_CATEGORIZED = "Not Applicable"

MATRIX_AXIS_MIN = 1
MATRIX_AXIS_MAX = 5
MATRIX_SCORE_MAX = 25

MATRIX_BAND_HIGH_MIN   = 15
MATRIX_BAND_MEDIUM_MIN = 10

FOREIGN_OFFICIAL_IMPACT_FLOOR = 4
MISSING_EXPECTED_EVIDENCE_LIKELIHOOD_FLOOR = 3
RECURRENCE_LIKELIHOOD_INCREMENT = 1

MATRIX_IMPACT_LABELS = {
    1: "Very Low",
    2: "Low",
    3: "Moderate",
    4: "High",
    5: "Very High",
}
MATRIX_LIKELIHOOD_LABELS = {
    1: "Very Low",
    2: "Low",
    3: "Moderate",
    4: "High",
    5: "Very High",
}

# ── Category rollup (Risk Summary: Overall Risk Category = average across risk types) ──
CATEGORY_RANK = {
    RISK_CATEGORY_HIGH:   3,
    RISK_CATEGORY_MEDIUM: 2,
    RISK_CATEGORY_LOW:    1,
    RISK_CATEGORY_NONE:   0,
}
CATEGORY_AVG_HIGH_MIN   = 2.5
CATEGORY_AVG_MEDIUM_MIN = 1.5
CATEGORY_AVG_LOW_MIN    = 0.5

# ── Output column names (Delta table) ─────────────────────────────────────────

# ── Excel output colours (hex, no #) ─────────────────────────────────────────

# ── SQL / Databricks warehouse ────────────────────────────────────────────────
DATABRICKS_WAREHOUSE_PATH_PREFIX  = "/sql/1.0/warehouses/"
SQL_SAMPLE_ROWS_IN_TABLE_INFO     = 3

# ── Agent names ───────────────────────────────────────────────────────────────
AGENT_HCBI_POLICY                = "hcbi_policy_agent"
AGENT_POLICY_KNOWLEDGE           = "policy_knowledge_agent"
AGENT_SUPPORTING_DOCS            = "supporting_docs_agent"
AGENT_RISK_EVALUATION            = "risk_evaluation_agent"
AGENT_TRANSACTIONAL              = "transactional_data_agent"
AGENT_RISK_CLASSIFIER            = "risk_classifier_agent"
AGENT_RISK_WRITER                = "risk_writer"
AGENT_SCOPE_IDENTIFIER           = "scope_identifier_agent"
AGENT_TRANSACTIONAL_ANALYSIS     = "transactional_data_analysis_agent"
AGENT_RISK_AGGREGATION           = "risk_aggregation_agent"
AGENT_PAST_AUDIT_SUMMARY         = "past_audit_summarizer_agent"

# ── Pipeline concurrency ───────────────────────────────────────────────────────
# GPT-5 tier-3 (RPM 30000, TPM 3,000,000). The binding limit is TPM: prompts are large, so each
# question consumes ~60-120k tokens across its ~6-8 LLM calls. 8 concurrent is a relaxed setting
# with ample TPM headroom that also avoids overloading the Databricks SQL warehouse / vector search.
QUESTION_CONCURRENCY             = 8
MAX_TRANS_RETRIES                = 3
# Hard cap on the per-question NL2SQL agent's reasoning/tool steps, so a single question can't run
# away on the SQL warehouse. Counts ALL agent steps (list-tables + schema + queries + final answer),
# so ~2-3 are fixed overhead (list-tables + schema) before actual queries. 4 was too low: the agent
# ran out of steps after a single query and returned "no data" for rows whose data actually exists
# (named-invoice / supplier / GL lookups need to try a LIKE match across BOTH payment + disbursement).
TRANSACTIONAL_SQL_MAX_ITERATIONS = 12

# ── Canonical scope-area display names ───────────────────────────────────────
# Internal scope_id → the agreed 48-item canonical display name. Applied everywhere a scope
# label is written (Output A, C, AI Scope) so all outputs use the same strings.
SCOPE_ID_TO_DISPLAY_NAME = {
    "CONGRESSES__JJ_ORGANIZED_EVENTS":                "Congresses & JJ Organized Events",
    "CONTRACT_SALES_AGENTS":                          "Contract Sales Agents",
    "DONATIONS":                                      "Donations",
    "DUE_DILIGENCE":                                  "Due Diligence",
    "EDUCATIONAL_GRANTS":                             "Educational Grants",
    "EQUIPMENT_PLACEMENT":                            "Equipment Placement",
    "EXPENSE_REPORTS":                                "Expense Reports",
    "FEE_FOR_SERVICE__DOCTOR_CONSULTING":             "Fee for Service (doctor consulting fees)",
    "FREE_GOODS":                                     "Free Goods",
    "PRODUCT_SAMPLES":                                "Product Samples",
    "PRICING__DISCOUNTS__MARGINS__DISTRIBUTORS":      "Pricing, Discounts, & Margins (discount to distributor/public hospital)",
    "PRICE_OVERRIDES":                                "Price Overrides",
    "CREDIT_NOTES":                                   "Credit Notes",
    "PAYMENTS_IN_AP_TO_DISTRIBUTORS":                 "Payments in AP to distributors (Non-Rebates)",
    "REBATES_PAID_TO_DISTRIBUTORS":                   "Rebates Paid to Distributors via Additional Invoice Discount",
    "PRODUCT_TRAINING":                               "Product Training",
    "SPONSORSHIPS":                                   "Sponsorships",
    "TENDERS":                                        "Tenders",
    "VALUE_ADD__PATIENT_SUPPORT_PROG":                "Value Add & Patient Support Programs",
    "AUDIT_FEES":                                     "Audit Fees",
    "CASH_ADVANCES__PETTY_CASH__MANUAL_CHECKS":       "Cash Advances / Petty Cash/ Manual Checks",
    "CLINICAL_TRIALS":                                "Clinical Trials",
    "CONTRACT_MANUFACTURING_VENDORS":                 "Contract Manufacturing Vendors",
    "FCPA_TRAINING":                                  "FCPA Training",
    "FORMER_GOVERNMENT_EMPLOYEES":                    "Former Government Employees",
    "GOVERNMENT_AFFAIRS":                             "Government Affairs",
    "HCC_ENVIRONMENT":                                "HCC Environment",
    "HCC_MONITORING":                                 "HCC Monitoring",
    "INTERACTION_WITH_PRODUCT_AND_SITE_REGULATORS":   "Interaction with Product and Site regulators",
    "JOINT_VENTURES_CO_PROMOTION":                    "Joint Ventures/Co-Promotion",
    "LEGAL_FEES":                                     "Legal Fees",
    "LOBBYISTS":                                      "Lobbyists",
    "LOCAL_TRAVEL_AGENCIES":                          "Local Travel Agencies",
    "LOGISTICS_INTERMEDIARIES__CUSTOMS":              "Logistics Intermediaries / Customs Brokers",
    "MARKETING_RESEARCH_PROJECTS":                    "Marketing Research Projects",
    "MEDIA_AND_PUBLIC_RELATIONS_VENDORS":             "Media and Public Relations Vendors",
    "ONE_TIME_VENDOR":                                "One Time Vendor",
    "PAYMENTS_TO_GOVERNMENT_AGENCIES":                "Payments to Government Agencies",
    "PAYMENTS_TO_GOVERNMENT_OFFICIALS":               "Payments to Government Officials / MOH scan",
    "PROCESS_TO_IDENTIFY_GOVERNMENT_OFFICIALS":       "Process to Identify Government Officials",
    "PROCUREMENT_FROM_GOVERNMENT_OWNED_ENTITIES":     "Procurement from Government Owned Entities",
    "PRODUCT_PROMOTION_VIA_THIRD_PARTY":              "Product Promotion via Third Party Intermediaries (formerly co-promotion)",
    "RESEARCH_GRANTS__INVESTIGATOR_INITIATED_STUDIES": "Research Grants / Investigator Initiated Studies (IIS)",
    "ROYALTIES_PAID_TO_PUBLIC_HCPS":                  "Royalties paid to public HCPs (healthcare professionals)",
    "TRAINING_FOR_POLICYMAKERS":                      "Training for Policymakers",
    "MARKET_ACCESS":                                  "Market Access",
    "PATIENT_ADVOCACY":                               "Patient Advocacy",
    "STRATEGIC_AFFAIRS":                              "Strategic Affairs",
}

# ── Excel workbook settings ───────────────────────────────────────────────────
SHEET_A_NAME                     = "Output A - Risk Detail"
SHEET_B_NAME                     = "Output B - Scope Summary"
SHEET_C_NAME                     = "Output C - AI Risk Analysis"
SHEET_D_NAME                     = "Output D - Risk Summary"
SHEET_E_NAME                     = "Output E - Past Audit Report"
SHEET_F_NAME                     = "Output F - Scope Txn Summary"
EXCEL_MAX_ROW_HEIGHT             = 150

# ── Ingestion: supported file extensions ──────────────────────────────────────
SUPPORTED_VOLUME_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".msg",
    ".html", ".htm", ".xlsx", ".xls", ".txt",
}

# ── Ingestion: PDF extractor settings ─────────────────────────────────────────
PDF_MIN_TEXT_CHARS               = 20
PDF_OCR_DPI                      = 300
PDF_PROGRESS_LOG_EVERY           = 10

# ── Ingestion: supported image formats for OCR (Azure DI + PIL compatible) ────
OCR_SUPPORTED_IMAGE_FORMATS      = {"PNG", "JPEG", "BMP", "TIFF", "HEIF", "GIF", "WEBP"}

# ── Ingestion: chunker section-header markers ─────────────────────────────────
SECTION_HEADER_MARKERS = (
    "SECTION", "CHAPTER", "PART ", "ARTICLE",
    "SCHEDULE", "ANNEX", "APPENDIX", "CLAUSE",
)

# ── Questionnaire repo: planning table column names ───────────────────────────
PLAN_COL_UNIQUE_ID               = "unique_id"
PLAN_COL_QUESTION                = "question"
PLAN_COL_COMPANY_RESPONSE        = "company_response"
PLAN_COL_FOLLOWUP_QUESTION       = "followup_question"
PLAN_COL_FOLLOWUP_RESPONSE       = "followup_response"

# ── Questionnaire repo: IDR table column names ────────────────────────────────
IDR_COL_ID                       = "question_no"
IDR_COL_QUESTION                 = "data_and_report_request"
IDR_COL_RESPONSE                 = "management_response"
IDR_COL_FOLLOWUP_QUESTION        = "followup_question"
IDR_COL_FOLLOWUP_RESPONSE        = "followup_response"

# ── Questionnaire types ────────────────────────────────────────────────────────

# ── Transactional analysis: TPI inventory ─────────────────────────────────────
TPI_INVENTORY_COL_ENTITY_NAME = "Third Party: Legal Entity Name (English)"

# ── Transactional analysis: payments table column names ───────────────────────

# ── Transactional analysis: revenue table column names ────────────────────────

# ── Transactional analysis: thresholds ────────────────────────────────────────


# ── Observability (Lakebase Postgres run tracing) ─────────────────────────────
OBS_SCHEMA = "public"
OBS_TABLE_RUN       = "obs_run"
OBS_TABLE_QUESTION  = "obs_question"
OBS_TABLE_LLM_CALL  = "obs_llm_call"
OBS_TABLE_FINDING   = "obs_finding"
OBS_TABLE_INGESTION = "obs_ingestion"

# Per-file ingestion/embedding status values (obs_ingestion.status).
OBS_INGEST_COMPLETED       = "completed"
OBS_INGEST_DOWNLOAD_FAILED = "download_failed"
OBS_INGEST_EXTRACT_FAILED  = "extract_failed"
OBS_INGEST_NO_CHUNKS       = "no_chunks"
OBS_INGEST_UPSERT_FAILED   = "upsert_failed"

# Run lifecycle status values.
OBS_STATUS_RUNNING   = "running"
OBS_STATUS_COMPLETED = "completed"
OBS_STATUS_FAILED    = "failed"
OBS_STATUS_PARTIAL   = "partial"

# Stage labels for an LLM call (attributes each call to a pipeline phase).
OBS_STAGE_PER_QUESTION          = "per_question"
OBS_STAGE_TRANSACTIONAL_ANALYSIS = "transactional_analysis"
OBS_STAGE_AGGREGATION           = "aggregation"
OBS_STAGE_PAST_AUDIT            = "past_audit"
OBS_STAGE_SETUP                 = "setup"

# Finding origin values.
OBS_ORIGIN_QUESTIONNAIRE = "questionnaire"
OBS_ORIGIN_AGGREGATED    = "aggregated"

# Question lifecycle status values (obs_question.status).
OBS_QSTATUS_COMPLETED = "completed"
OBS_QSTATUS_SKIPPED   = "skipped"
OBS_QSTATUS_ERROR     = "error"

# Per-LLM-call status values (obs_llm_call.status).
OBS_CALL_STATUS_OK    = "ok"
OBS_CALL_STATUS_ERROR = "error"
OBS_DEFAULT_ATTEMPT   = 1

# Lakebase connection timeout (seconds).
OBS_CONNECT_TIMEOUT_S = 30

# Pricing map dict keys (single source shared by the map below and the cost calculator).
PRICE_KEY_INPUT        = "input"
PRICE_KEY_CACHED_INPUT = "cached_input"
PRICE_KEY_OUTPUT       = "output"

# Model pricing — USD per 1 MILLION tokens, per deployment. Cost = tokens / 1e6 * rate.
# Cached input tokens (prompt-cache hits) are billed at a reduced rate when the model reports them.
# Keep model keys lowercase; lookups are case-insensitive with a safe zero-cost fallback.
MODEL_PRICING_PER_MILLION = {
    GPT5_DEPLOYMENT: {
        PRICE_KEY_INPUT:        1.25,
        PRICE_KEY_CACHED_INPUT: 0.125,
        PRICE_KEY_OUTPUT:       10.00,
    },
    # Embeddings are billed on input tokens only (no output / no cache tier).
    EMBEDDING_DEPLOYMENT.lower(): {
        PRICE_KEY_INPUT: 0.13,
    },
}
MODEL_PRICING_TOKEN_UNIT = 1_000_000

# ── Translation service (src/services/translation.py) ─────────────────────────
# Language detection / when-to-translate thresholds
TRANSLATE_MIN_NONLATIN_RATIO = 0.12   # long text: translate only when >=12% of chars are non-Latin
TRANSLATE_SHORT_LEN          = 40     # <= this many chars: any non-Latin char triggers (names/cells)
TRANSLATE_BATCH_SHORT_LEN    = 200    # strings up to this length go through the JSON batch path
TRANSLATE_DOC_CHUNK_CHARS    = 6_000  # per-LLM-call size for long-document translation
TRANSLATE_BATCH_SIZE         = 40     # unique short snippets per batch call
TRANSLATE_WORKERS            = 6      # concurrent chunk translations per document (override in settings)
TRANSLATE_PROGRESS_EVERY     = 10     # log a translation-progress line every N completed chunks
LANGDETECT_MIN_CHARS         = 60     # min chars before langdetect is trusted (skips short names/cells)
LANGDETECT_SAMPLE_CHARS      = 2000   # cap the text slice fed to langdetect (enough to decide; fast)

# ── Azure AI Translator (Text v3.0) engine ────────────────────────────────────
AZURE_TRANSLATOR_GLOBAL_ENDPOINT      = "https://api.cognitive.microsofttranslator.com"
AZURE_TRANSLATOR_API_VERSION          = "3.0"      # Text API version (query param on every call)
AZURE_TRANSLATOR_BATCH_CHARS          = 45_000     # per-request char budget (Azure hard limit ~50k)
AZURE_TRANSLATOR_BATCH_ITEMS          = 900        # per-request array size (Azure limit ~1000)
AZURE_TRANSLATOR_PROGRESS_EVERY       = 10         # progress line every N batches (big docs)
AZURE_TRANSLATOR_MAX_RETRIES          = 5          # per-batch SSL/network/timeout/5xx retries
AZURE_TRANSLATOR_RETRY_BACKOFF        = 2.0        # seconds, multiplied by attempt number (2s, 4s, …)
AZURE_TRANSLATOR_THROTTLE_RETRIES     = 12         # 429 retries — honours Retry-After, never drops a batch
AZURE_TRANSLATOR_THROTTLE_MAX_WAIT    = 60         # cap (s) for one throttle wait when no Retry-After
AZURE_TRANSLATOR_MAX_CONCURRENCY      = 1          # max simultaneous requests across all workers
AZURE_TRANSLATOR_MIN_INTERVAL         = 1.0        # min seconds between consecutive request starts (anti-429)
AZURE_TRANSLATOR_CHARS_PER_MINUTE     = 600_000    # sustained chars/min ceiling (token bucket) — primary anti-429
AZURE_TRANSLATOR_BUCKET_BURST_SECONDS = 2.0        # seconds of burst the token bucket allows
AZURE_TRANSLATOR_CONNECT_TIMEOUT      = 10         # seconds to establish the connection
AZURE_TRANSLATOR_READ_TIMEOUT         = 180        # seconds to wait for the (possibly throttled) response
AZURE_TRANSLATOR_ENDPOINT_PROBE_TIMEOUT = 20       # seconds for the one-time endpoint-reachability probe
AZURE_TRANSLATOR_BUCKET_WAIT_MIN_S    = 0.05       # token-bucket re-check floor (avoid busy-spin)
AZURE_TRANSLATOR_BUCKET_WAIT_MAX_S    = 5.0        # token-bucket re-check ceiling (re-refill periodically)
