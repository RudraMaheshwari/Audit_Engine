from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The 7 dashboards analysed by default (the SAM audit set). Additional dashboards are registered in
# all_dashboard_table_names and enabled per audit via the ACTIVE_DASHBOARDS env var.
_DEFAULT_ACTIVE_DASHBOARDS = (
    "disbursement", "payment", "hcc", "otc", "pricing", "revenue", "travel_expense",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure OpenAI (LLM)
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment: str = "gpt-5"
    azure_openai_api_version: str = "2024-10-21"

    # Azure OpenAI (Embeddings — separate endpoint and key)
    azure_embedding_endpoint: str
    azure_embedding_api_key: str
    azure_embedding_deployment: str = "text-embedding-3-large"
    embedding_dimensions: int = 3072

    # Azure Document Intelligence (OCR)
    azure_document_intelligence_endpoint: str = ""
    azure_document_intelligence_key: str = ""

    # OCR — Tesseract fallback
    tesseract_cmd: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    # Databricks platform — service-principal OAuth (M2M); no PAT
    databricks_host: str
    databricks_client_id: str
    databricks_client_secret: str
    databricks_catalog: str = "nbod_gaagd"
    databricks_schema_name: str = "cpr_risk_analysis"
    # Schema (in databricks_catalog) where the audit output sheets are mirrored as Delta tables.
    risk_analysis_output_schema: str = "risk_analysis_output"
    # UC Volumes (under the output schema) where the output FILES are uploaded. Empty disables upload.
    # Risk Analysis + AI Scope: a NEW timestamped copy per run (<engagement>_<label>_<timestamp>).
    risk_analysis_output_volume_path: str = (
        "/Volumes/nbod_gaagd/risk_analysis_output/risk_analysis_output"
    )
    ai_scope_output_volume_path: str = (
        "/Volumes/nbod_gaagd/risk_analysis_output/ai_scope_outputs"
    )
    # Scoping Deck: OVERWRITTEN each run with the deck's own name (same entity -> same name -> replaced).
    scoping_deck_output_volume_path: str = (
        "/Volumes/nbod_gaagd/risk_analysis_output/scoping_deck"
    )
    databricks_transactional_schema: str = "cpr__gld"
    databricks_vector_search_endpoint: str = "cpr_risk_analysis"
    databricks_warehouse_id: str

    # Current auditor being processed — scopes per-auditor volumes and index metadata
    auditor_id: str = "Sam_1"

    # Vector index names (v2 — fresh names so old indexes are simply unused)
    hcbi_index_name: str = "hcbi_policy_index"
    # Global scope-area policy index (formerly "tpi_due_diligence_index").
    policy_index_name: str = "global_scope_area_policies"
    supporting_docs_index_name: str = "auditor_supporting_docs_index"
    fcpa_scope_policy_index_name: str = "fcpa_scope_policy_index"

    # Knowledge-base indexes (scoped per auditor via auditor_id metadata)
    transactional_analysis_index_name: str = "transactional_analysis_index"
    past_audit_index_name: str = "past_audit_index"
    si_history_index_name: str = "si_history_index"
    legal_index_name: str = "legal_index"
    additional_input_index_name: str = "additional_input_index"
    local_policies_index_name: str = "local_policies_index"
    raw_transactional_tables_index_name: str = "raw_transactional_tables_index"
    # SHARED with the Scoping Deck — same physical index/name so the main app reads the same Q&A corpus.
    questionnaire_index_name: str = "questionnaire_index"

    # Databricks volume paths (global knowledge sources). Defaulted like the sibling volume paths so a
    # missing HCBI_VOLUME_PATH / POLICY_VOLUME_PATH no longer crashes EVERY engine (incl. Scoping Deck,
    # which never uses them). Override via .env / Key Vault when the real volume names differ.
    hcbi_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/global_policies"
    policy_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/global_scope_area_policies"

    # Auditors supporting documents — parent folder containing one subfolder per auditor
    auditors_supporting_docs_volume_path: str = (
        "/Volumes/nbod_gaagd/cpr_risk_analysis/auditors_supporting_docs"
    )

    # Scope area policy Excel (CPR guide book — all 48 FCPA scope areas)
    scope_area_policy_file_path: str = (
        "/Volumes/nbod_gaagd/cpr_risk_analysis/fcpa_scope_area_guide_book"
        "/CPR AI - System Guide.xlsx"
    )

    # Knowledge-base volume paths (parent folders; one subfolder per auditor)
    # SI History, Legal and Additional Input are stored in separate subfolders:
    #   si-history/ , legal/ and additional-input/
    si_history_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/si-history"
    legal_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/legal"
    additional_input_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/additional-input"
    # Where the ENGLISH translation of each non-English ingested doc is saved as a .docx (one subfolder
    # per auditor). Underscore name (not 'translated-docs') to match the other output volumes / UC rules.
    translated_docs_volume_path: str = "/Volumes/nbod_gaagd/risk_analysis_output/translated_docs"
    past_audit_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/past-audit-data"
    transactional_table_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/transactional_table"
    local_policies_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/local_policies"
    questionnaire_volume_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/questionnaire_data"

    # Transactional dashboard tables (the 7 in-use GLD dashboards, in the transactional schema)
    disbursement_table: str = "gld__disbursement_dashboard__cpr"
    payment_dashboard_table: str = "gld__payment_dashboard__cpr"
    hcc_dashboard_table: str = "gld__user_input__hcc_dashboard__cpr"
    otc_dashboard_table: str = "gld__user_input__otc_dashboard__cpr"
    pricing_dashboard_table: str = "gld__user_input__pricing_dashboard__cpr"
    revenue_dashboard_table: str = "gld__user_input__revenue_dashboard__cpr"
    travel_expense_dashboard_table: str = "gld__user_input__travel_and_expense_dashboard__cpr"

    # Additional GLD dashboards — registered in the full table map but NOT analysed for SAM (they hold
    # other auditors' / other audits' data). Activated per audit via ACTIVE_DASHBOARDS.
    hcc_full_dashboard_table: str = "gld__hcc_dashboar__cpr"
    hcc_attendee_dashboard_table: str = "gld__hcc_attendee_dashboard__cpr"
    travel_expense_attendee_dashboard_table: str = "gld__travel_and_expense_attendee_dashboard__cpr"

    # Comma-separated dashboard keys analysed for THIS audit. Empty -> the 7 SAM defaults.
    active_dashboards: str = ""

    # Audit engagement number(s) — the UNIQUE per-audit population key. Present as ENGAGEMENT_NUMBER
    # (INT) in EVERY transactional dashboard, which hold many audits' rows. This scopes ALL
    # transactional analysis (calc queries, sample queries, and data summaries) to THIS audit's rows.
    # Every dashboard row carries it and there is NO fallback column. Comma-separated to allow
    # multiple engagements per audit. Supplied per run via the API (falls back to AUDIT_ENGAGEMENT_
    # NUMBERS in .env for CLI runs). Optional at construction; the transactional analysis requires it
    # at run time (an empty value yields an empty engagement filter).
    audit_engagement_numbers: str = ""

    # Legacy transactional Delta table names (superseded by the 7 dashboards above)
    payments_table: str = "payments_dashboard_summary_im_australia"
    credit_memo_table: str = "revenue_dashboard_credit_memo_returns_im_australia"
    revenue_table: str = "revenue_dashboard_summary_im_australia"

    # Input questionnaire table names (single-table defaults; multi-table overrides via idr_tables /
    # planning_tables below, set per run from the API).
    idr_table: str = "idr_sam_1"
    planning_table_name: str = "consolidated_sam_1"
    # Per-run questionnaire tables (comma-separated; one or more each). Empty -> the single names above.
    idr_tables: str = ""
    planning_tables: str = ""

    # Per-run ingestion folders (full auditor paths, set by the API). Empty -> derived from the base
    # volume + auditor_id (see the resolver properties below).
    supporting_docs_path: str = ""
    si_history_path: str = ""
    legal_path: str = ""
    additional_input_path: str = ""
    past_audit_path: str = ""

    # Reference files loaded directly from volume (not indexed)
    # TPI INVENTORY RESOLUTION ORDER (see src/pipeline/runner._resolve_tpi_inventory_path):
    #   1) Auto-discovered in the auditor's SUPPORTING DOCS folder by the TC naming convention
    #      SD_<Engagement_no>_IDR_<Section_ID>_Q#_<Filename>  (the <Filename> part identifies it as
    #      the TPI inventory, e.g. "...TPI Inventory List.xlsx").
    #   2) Else this explicit path, when provided via the API (optional fallback).
    #   3) Else skipped entirely — TPI name-matching degrades gracefully (no inventory loaded).
    # Leave EMPTY (the default) unless the caller wants to force a specific file.
    tpi_inventory_file_path: str = ""
    # Master TPI Inventory List.xlsx holds one sheet per auditor (same column layout):
    #   sam1-4.3, Naeem1-4.3, Naeem2-4.3, Naeem3-4.3, Kaitlyn1-4.3, Kaitlyn2-4.3, Leander1-4.3
    # Leave EMPTY to auto-derive the sheet from the auditor being run (e.g. Sam_1 -> sam1-4.3).
    # Set an explicit value only to force a specific sheet (manual override).
    tpi_inventory_sheet: str = ""

    # Translate non-English ingested text (supporting docs, SI/Legal/Additional/past-audit, questionnaire
    # Q&A, TPI names) to English via Azure OpenAI. Auto-detected per text — English passes through
    # untouched. Best-effort: a failure keeps the original text. Set False to disable entirely.
    translate_to_english: bool = True
    # Concurrent chunk translations per document (speeds up large non-English files; nothing is
    # skipped — a failed chunk keeps its original text). Lower it if you hit Azure OpenAI rate limits.
    translation_workers: int = 6
    # Translation engine: "azure_translator" (PRIMARY — dedicated NMT, far faster/cheaper for bulk, no
    # TPM wall) or "openai" (LLM). azure_translator is used only when azure_translator_key is set AND an
    # endpoint is reachable. There is NO OpenAI fallback for the azure_translator engine — a failed
    # batch keeps its original text (no data loss). The endpoint auto-falls back from the custom host to
    # the global host.
    translation_engine: str = "azure_translator"
    azure_translator_endpoint: str = ""   # set via env AZURE_TRANSLATOR_ENDPOINT (no host hardcoded)
    azure_translator_key: str = ""        # set via env / the 'cpr-audit' secret scope (never hardcode)
    azure_translator_region: str = "eastus"
    # Anti-429 pacing for Azure Translator (committed 250M-char/month tier). These cap how hard we hit
    # the endpoint so we stay under the per-minute throttle and keep a safety gap:
    #   * max_concurrency   — simultaneous requests across ALL file-workers (1 = fully serialised; raise
    #                         only with throughput headroom while still seeing zero 429s).
    #   * min_request_interval — minimum seconds between the START of consecutive requests (the GAP that
    #                         smooths the character rate). ~45k chars/request, so 1.0s ⇒ ≤ ~2.7M chars/min.
    translation_max_concurrency: int = 1
    translation_min_request_interval: float = 1.0
    # PRIMARY anti-429 control: a sustained character-rate ceiling (chars/MINUTE) enforced by a token
    # bucket, so we pace PROACTIVELY just under the resource's throttle instead of bouncing off it with
    # backoff. The monthly commitment (e.g. 250M/month) is a BILLING bucket, NOT this rate — Azure
    # throttles on a fixed sliding chars-per-minute window per resource. Tune to taste: if you still see
    # 429s, lower it; if you see none, raise it. 0 disables the limiter. Observed throttle on
    # gaa-audit-dev was < ~1.5M/min, so 900k leaves a safety gap.
    translation_chars_per_minute: int = 900_000

    # Local language for BILINGUAL answers in Output A: when set (e.g. "Japanese"), each finding's
    # Risk Description + Risk Rationale is rendered in English AND this language (stacked). Output C and
    # all machine-consumed outputs stay English-only. Empty = English only (no change).
    audit_local_language: str = ""
    hcc_gl_account_map_path: str = "/Volumes/nbod_gaagd/cpr_risk_analysis/global_policies/HCC_GL_Account_Map-11142023.xlsx"

    # Reference files (TPI inventory, GL map) are cached locally after first download; set REFRESH_
    # REFERENCE_FILES=true to force a re-download (e.g. when the inventory is updated on the volume).
    refresh_reference_files: bool = False

    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 100

    # Retrieval
    top_k_hcbi: int = 5
    top_k_policy: int = 5
    top_k_supporting: int = 5
    # Targeted retrieval of a SINGLE cited supporting doc pulls far more chunks than the broad search,
    # so a large cited file (e.g. a 2,500-row deviation / red-flag log) is represented by many entries —
    # not just 5 — and its occurrence / corrective-action / follow-up detail reaches the writer. Bounded
    # to one file, so a high value adds coverage without adding cross-document noise.
    top_k_supporting_targeted: int = 30
    top_k_scope_policy: int = 2
    conflict_similarity_threshold: float = 0.90

    # Displayed page numbers run one ahead of the printed page, so shift cited pages by this offset.
    # Applied at retrieval so every citation (all sheets) is corrected consistently. Set 0 to disable.
    page_number_display_offset: int = 0

    # Scope
    current_scope_id: str = "fcpa_all_scopes"
    total_scope_areas: int = 48

    # Audit identity passed per run as CONTEXT for the LLM (so analysis knows WHICH entity and WHICH
    # period it is reviewing). All optional — blank values are simply omitted from the context line.
    # scope_period_* are ISO date strings (YYYY-MM-DD) set from the Risk Analysis request.
    entity_name: str = ""
    scope_period_start: str = ""
    scope_period_end: str = ""

    @property
    def audit_scope_context(self) -> str:
        """One-line audit-context preamble for the per-question analysis, or '' when nothing is set."""
        bits: list[str] = []
        if self.entity_name:
            bits.append(f"Entity under audit: {self.entity_name}.")
        if self.scope_period_start and self.scope_period_end:
            bits.append(f"Audit scope period: {self.scope_period_start} to {self.scope_period_end} "
                        f"(the past period under review — all analysis concerns THIS period).")
        elif self.scope_period_start:
            bits.append(f"Audit scope period starts {self.scope_period_start}.")
        elif self.scope_period_end:
            bits.append(f"Audit scope period ends {self.scope_period_end}.")
        return ("AUDIT CONTEXT: " + " ".join(bits)) if bits else ""

    # Audit local currency FALLBACK only. The primary value is resolved AT RUNTIME from the data
    # (most common non-USD currency code for the engagement — i.e. where the audit runs). This default
    # is used solely when no currency column/value can be found. Used by the per-scope summary sheet.
    audit_local_currency: str = "AUD"

    # Observability (Lakebase Postgres run tracing). Disabled by default so a normal run is
    # unaffected until explicitly turned on. When enabled, the schema is created on first use.
    obs_enabled: bool = False
    lakebase_instance_name: str = ""
    lakebase_database: str = "databricks_postgres"
    lakebase_user: str = ""
    lakebase_port: int = 5432
    lakebase_sslmode: str = "require"
    # 0 = store full request/response payloads on obs_llm_call; a positive value truncates them.
    obs_payload_max_chars: int = 0
    app_version: str = ""

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("azure_openai_endpoint", "databricks_host", "azure_embedding_endpoint")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("conflict_similarity_threshold")
    @classmethod
    def validate_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("conflict_similarity_threshold must be between 0.0 and 1.0")
        return v

    @field_validator("chunk_size")
    @classmethod
    def validate_chunk_size(cls, v: int) -> int:
        if not 100 <= v <= 4000:
            raise ValueError("chunk_size must be between 100 and 4000 tokens")
        return v

    @field_validator("embedding_dimensions")
    @classmethod
    def validate_embedding_dims(cls, v: int) -> int:
        if v not in {256, 512, 1536, 3072}:
            raise ValueError("embedding_dimensions must be one of 256, 512, 1536, 3072")
        return v

    @field_validator("audit_engagement_numbers")
    @classmethod
    def validate_engagement_numbers(cls, v: str) -> str:
        # Optional (supplied per run via the API). When present, every entry must be an integer.
        parts = [p.strip() for p in (v or "").split(",") if p.strip()]
        for p in parts:
            if not p.lstrip("-").isdigit():
                raise ValueError(f"audit_engagement_numbers must be integers; got {p!r}")
        return v

    # ── Computed properties (fully-qualified table names) ─────────────────────

    @property
    def full_payments_table(self) -> str:
        return f"{self.databricks_catalog}.{self.databricks_schema_name}.{self.payments_table}"

    @property
    def full_credit_memo_table(self) -> str:
        return f"{self.databricks_catalog}.{self.databricks_schema_name}.{self.credit_memo_table}"

    @property
    def full_revenue_table(self) -> str:
        return f"{self.databricks_catalog}.{self.databricks_schema_name}.{self.revenue_table}"

    @property
    def transactional_tables(self) -> dict[str, str]:
        return {
            "payments": self.full_payments_table,
            "credit_memo": self.full_credit_memo_table,
            "revenue": self.full_revenue_table,
        }

    def _full_transactional(self, table: str) -> str:
        return f"{self.databricks_catalog}.{self.databricks_transactional_schema}.{table}"

    @property
    def all_dashboard_table_names(self) -> dict[str, str]:
        """Every known GLD dashboard (all 10), regardless of whether it is active for this audit."""
        return {
            "disbursement":            self.disbursement_table,
            "payment":                 self.payment_dashboard_table,
            "hcc":                     self.hcc_dashboard_table,
            "otc":                     self.otc_dashboard_table,
            "pricing":                 self.pricing_dashboard_table,
            "revenue":                 self.revenue_dashboard_table,
            "travel_expense":          self.travel_expense_dashboard_table,
            "hcc_full":                self.hcc_full_dashboard_table,
            "hcc_attendee":            self.hcc_attendee_dashboard_table,
            "travel_expense_attendee": self.travel_expense_attendee_dashboard_table,
        }

    @property
    def active_dashboard_keys(self) -> tuple[str, ...]:
        """Dashboard keys analysed for this audit; defaults to the 7 SAM dashboards when unset."""
        raw = (self.active_dashboards or "").strip()
        if not raw:
            return _DEFAULT_ACTIVE_DASHBOARDS
        known = self.all_dashboard_table_names
        return tuple(k.strip() for k in raw.split(",") if k.strip() in known)

    @property
    def dashboard_table_names(self) -> dict[str, str]:
        """The dashboards analysed for this audit (active subset)."""
        known = self.all_dashboard_table_names
        return {key: known[key] for key in self.active_dashboard_keys}

    @property
    def dashboard_tables(self) -> dict[str, str]:
        return {
            key: self._full_transactional(name)
            for key, name in self.dashboard_table_names.items()
        }

    @property
    def all_dashboard_tables(self) -> dict[str, str]:
        """Fully-qualified names for every known dashboard (all 10)."""
        return {
            key: self._full_transactional(name)
            for key, name in self.all_dashboard_table_names.items()
        }

    # ── Audit population (engagement number) ──────────────────────────────────

    @property
    def engagement_numbers(self) -> tuple[int, ...]:
        """Parsed engagement numbers for this audit (ints). Empty tuple if unset/invalid."""
        out: list[int] = []
        for part in (self.audit_engagement_numbers or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                continue
        return tuple(out)

    @property
    def engagement_sql_in_list(self) -> str:
        """SQL IN-list of engagement numbers from .env (ints, unquoted, comma-joined)."""
        return ", ".join(str(n) for n in self.engagement_numbers)

    @property
    def engagement_slug(self) -> str:
        """Engagement number(s) joined by '_' for naming output files (e.g. '1000_1001'). Falls back
        to the auditor_id when no engagement number is configured."""
        return "_".join(str(n) for n in self.engagement_numbers) or self.auditor_id

    # ── Per-run questionnaire tables + ingestion folders (API-supplied, else derived) ─────────

    @staticmethod
    def _csv_list(value: str) -> list[str]:
        return [p.strip() for p in (value or "").split(",") if p.strip()]

    @property
    def idr_table_list(self) -> list[str]:
        """IDR questionnaire table(s): the per-run list if supplied, else the single idr_table."""
        return self._csv_list(self.idr_tables) or ([self.idr_table] if self.idr_table else [])

    @property
    def planning_table_list(self) -> list[str]:
        """Planning questionnaire table(s): the per-run list if supplied, else planning_table_name."""
        return self._csv_list(self.planning_tables) or (
            [self.planning_table_name] if self.planning_table_name else []
        )

    def _auditor_folder_for(self, explicit: str, base: str) -> str:
        """Explicit per-run path when set, else base-volume/auditor_id (derived)."""
        return explicit.strip() if explicit and explicit.strip() else self._auditor_folder(base)

    @property
    def resolved_supporting_docs_folder(self) -> str:
        return self._auditor_folder_for(self.supporting_docs_path, self.auditors_supporting_docs_volume_path)

    @property
    def resolved_si_history_folder(self) -> str:
        return self._auditor_folder_for(self.si_history_path, self.si_history_volume_path)

    @property
    def resolved_legal_folder(self) -> str:
        return self._auditor_folder_for(self.legal_path, self.legal_volume_path)

    @property
    def resolved_additional_input_folder(self) -> str:
        return self._auditor_folder_for(self.additional_input_path, self.additional_input_volume_path)

    @property
    def resolved_past_audit_folder(self) -> str:
        return self._auditor_folder_for(self.past_audit_path, self.past_audit_volume_path)

    @property
    def engagement_filter_clause(self) -> str:
        """Mandatory audit-population predicate for EVERY transactional query, of the form
        'ENGAGEMENT_NUMBER IN (<numbers from .env>)'. Empty string only if none configured."""
        nums = self.engagement_sql_in_list
        return f"ENGAGEMENT_NUMBER IN ({nums})" if nums else ""

    def _auditor_folder(self, parent: str) -> str:
        return f"{parent.rstrip('/')}/{self.auditor_id}"

    @property
    def si_history_auditor_path(self) -> str:
        return self._auditor_folder(self.si_history_volume_path)

    @property
    def legal_auditor_path(self) -> str:
        return self._auditor_folder(self.legal_volume_path)

    @property
    def additional_input_auditor_path(self) -> str:
        return self._auditor_folder(self.additional_input_volume_path)

    @property
    def past_audit_auditor_path(self) -> str:
        return self._auditor_folder(self.past_audit_volume_path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
