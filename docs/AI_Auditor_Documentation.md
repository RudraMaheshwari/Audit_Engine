# AI Auditor — Complete Project Documentation

---

## 1. Project Overview

### Executive Summary
The AI Auditor is an automated audit intelligence system built for Johnson & Johnson (JNJ) to review Third Party Intermediary (TPI) compliance audits. It processes questionnaire responses from two sources — TPI entities and JNJ Management — validates them against policy documents, identifies compliance risks, and maps each finding to one of 48 audit scope areas.

The system replaces manual auditor effort for the initial review pass, producing a structured Excel/Delta output with risk classification, scope mapping, and source evidence for each question.

### Business Context
| Property | Value |
|---|---|
| **Client** | Johnson & Johnson (JNJ) |
| **External Parties** | Third Party Intermediaries (TPIs) |
| **Internal Parties** | JNJ Management team |
| **Regulatory Framework** | HCBI, FCPA, anti-bribery laws |
| **Audit Focus (Current Phase)** | TPI Due Diligence scope area |
| **Platform** | Databricks (Unity Catalog) on Azure |
| **Catalog** | `cat_cbt_gaa_brz_dev` |

### Questionnaire Types
| Questionnaire | Sent To | Purpose |
|---|---|---|
| **Planning Questionnaire** | TPI | Collect TPI responses on compliance controls and processes |
| **IDR (Initial Document Request)** | JNJ Management | Collect management's view on compliance, risk, and evidence |

---

## 2. Data Sources

### 2.1 Global Policies — HCBI Guide + HCC Framework
Both documents are category **"Global policy"** and share the same volume folder and the same vector index (`hcbi_index`).

| # | Document | Databricks Path | Notes |
|---|---|---|---|
| 1.4 | HCBI Guide | `/Volumes/cat_cbt_gaa_brz_dev/default/global_policies/TV-POL-02257_v3.0.pdf` | 115 pages, image-based PDF — requires OCR |
| 1.5 | HCC Framework | `/Volumes/cat_cbt_gaa_brz_dev/default/global_policies/TV-POL-02312_v2.0.pdf` | Global policy — indexed alongside HCBI |

**Priority:** Highest — overrides all other sources. Both files are in the same folder (`global_policies`) and ingested together into `hcbi_index`.

**HCBI Guide Sections:** Introduction · Anti-corruption & Ethical Business Policy Statement · Key Principles · Summary of Relevant Laws (FCPA, Anti-Bribery, Books & Records, Penalties) · Third-party Intermediary Risk Management · Needs Assessment / Fair Market Value · Cross-border Interactions · Facilitating Payments · Political & Charitable Contributions · International Joint Ventures · Record-keeping · Hiring · Reporting Violations · Managing HCC Incidents · Exceptions · Glossary

### 2.2 TPI Due Diligence Policies (Local)
| Property | Value |
|---|---|
| **Databricks Path** | `/Volumes/cat_cbt_gaa_brz_dev/default/local_policies_tpi` |
| **Priority** | Second — applies when not conflicting with global policies |
| **Notes** | Currently 17 policy files. Process recursively if subfolders are added. |

| File | Description |
|---|---|
| `3 Considerations_Exclusion_Reputational_Check_Process...` | Exclusion and reputational check process |
| `BGS ON INDIVIDUALS_ONEPAGER.pdf` | Background screening on individuals |
| `FCPA Language proposed final.doc` | FCPA contractual language |
| `ROLES AND RESPONSIBILITIES_TPI DD 1.pdf` | TPEC roles — GM, BUL, HCC Sp., HCCO, LWG, RWG |
| `Red Flag Management Process_FIRST_guidance.pdf` | Red flag review process, COI decision trees |
| `TPI MONITORING TOOLKIT_FINAL 1.pdf` | TPI monitoring — Data Monitoring, Onsite Visit, Process Monitoring |
| `TV-SOP-04690_v9.0.pdf` | Standard operating procedure for TPI due diligence |

### 2.3 Supporting Documents
| Property | Value |
|---|---|
| **Databricks Path** | `/Volumes/cat_cbt_gaa_brz_dev/default/supporting_documents` |
| **Priority** | Third — evidence for responses |
| **Notes** | Files may be in root OR nested sub-folders → **always scan recursively** |

**Supported formats:** `.pdf` · `.xls` / `.xlsx` · `.ppt` / `.pptx` · `.docx` · `.html` · `.msg` (with recursive attachment extraction)

### 2.3a Reference Files (inside supporting_documents — loaded directly, not indexed)
| # | File | Databricks Path | Used by |
|---|---|---|---|
| 1.8 | TPI Inventory Final Output | `/Volumes/cat_cbt_gaa_brz_dev/default/supporting_documents/TPI_Inventory_Final_Output.xlsx` | TransactionalDataAnalysisAgent — TPI matching |
| 1.9 | HCC GL Account Map | `/Volumes/cat_cbt_gaa_brz_dev/default/supporting_documents/HCC_GL_Account_Map-11142023.xlsx` | TransactionalDataAnalysisAgent — GL validation |

### 2.4 Master Audit Skills
| Property | Value |
|---|---|
| **Volume Path** | `/Volumes/cat_cbt_gaa_brz_dev/default/mastering_audit_skills` |
| **File Format** | `.pdf` — presentation-style PDFs (slide decks converted to PDF; contain both text and embedded diagrams/charts) |
| **Extraction** | `pdfplumber` + `PyMuPDF` for text; `pytesseract` / Azure Document Intelligence OCR for embedded images and diagrams |
| **Storage** | Extracted text stored in Delta table `master_agent_input_context` |
| **Role** | Loaded into Supervisor Agent system prompt as auditing methodology context |

| File | Size | Content |
|---|---|---|
| `Mastering Audit Skills_MODULE 1 - Introduction to CPR 2.pdf` | 1.17 MB | CPR framework, FCPA intro, Audit Timeline, Audit Program Guidance, Communication Protocols |
| `Mastering Audit Skills_MODULE 2 - Audit Planning and Risk Assessment 2.pdf` | 2.36 MB | Risk assessment methodology, audit planning |
| `Mastering Audit Skills_MODULE 3 - Audit Execution 2.pdf` | 1.44 MB | Evidence gathering, fieldwork, testing procedures |
| `Mastering Audit Skills_MODULE 4 - Audit Reporting 1 2.pdf` | 2.10 MB | Findings documentation, reporting standards |

**Loading Mechanism:**
1. All 4 PDF files are processed through `pdf_extractor.py` — hybrid mode: `pdfplumber` for text layers + OCR for embedded slide diagrams/images
2. Extracted text is chunked section-by-section and stored in the `master_agent_input_context` Delta table
3. At pipeline startup, all rows from `master_agent_input_context` are read and concatenated into the Supervisor Agent's system prompt
4. If audit skills content proves useful for retrieval-based risk analysis in future phases, a dedicated `audit_skills_index` can be created following the same `{scope_name}_index` convention

**Note:** Existing volumes `extracted_mastering_audit`, `extracted_text`, and `extracted_png` on the cluster suggest partial extraction has already been performed. Review these before re-running ingestion to avoid duplicating work.

### 2.5 Databricks Delta Tables
All paths confirmed from Databricks_Data_Path_Reference document.

| # | Table | Full Path | Role |
|---|---|---|---|
| 1.1 | `payments_dashboard_summary` | `cat_cbt_gaa_brz_dev.default.payments_dashboard_summary` | Raw transactional — Payments |
| 1.2 | `revenue_dashboard_credit_memo` | `cat_cbt_gaa_brz_dev.default.revenue_dashboard_credit_memo` | Raw transactional — Credit Memo / Returns |
| 1.3 | `revenue_dashboard_summary` | `cat_cbt_gaa_brz_dev.default.revenue_dashboard_summary` | Raw transactional — Revenue |
| 1.10 | `final_idr_compiles_sam_1` | `cat_cbt_gaa_brz_dev.default.final_idr_compiles_sam_1` | Input — IDR Questionnaire |
| 1.11 | `compiled_file_for_planning_questions_sam_1` | `cat_cbt_gaa_brz_dev.default.compiled_file_for_planning_questions_sam_1` | Input — Planning Questionnaire |
| — | `compliance_scope_description` | `cat_cbt_gaa_brz_dev.default.compliance_scope_description` | Reference — 48 scope embeddings |
| — | `master_agent_input_context` | `cat_cbt_gaa_brz_dev.default.master_agent_input_context` | Reference — Audit Skills (loaded into Supervisor prompt) |
| — | `compiled_file_scope_risk_assessment` | `cat_cbt_gaa_brz_dev.default.compiled_file_scope_risk_assessment` | Output (future — local Excel for now) |

**Note:** Transactional tables are queried by two separate agents: `transactional_data_agent` (NL-to-SQL cross-validation from questionnaire) and `transactional_data_analysis_agent` (rule-based data analysis). No vector index is created for transactional data.

### 2.6 Transactional Data Table Schemas

**Table 1 — PAYMENTS**
Source file: `Payments Dashboard_Summary_IM Australia.xlsx`

| Column | Notes |
|---|---|
| `Row Key` | Row identifier |
| `Supplier Invoice Number` | |
| `Invoice Number` | |
| `Invoice Document Date` | |
| `Invoice Clearing Date` | |
| `Invoice Posting Date` | |
| `GL Account Code` | |
| `GL Account Description` | |
| `Company Code` | |
| `Cost Center Name` | |
| `Doc. Description` | |
| `Supplier ID` | |
| `Supplier Name` | |
| `Final Audit Area` | Maps to audit scope area |
| `Invoice Description` | |
| `PO Number` | |
| `PO Description` | |
| `Audit Area Keywords` | Used to detect transactional context in questions |
| `Document Payment Currency` | |
| `Document Currency Invoice Amt` | Key quantitative KPI — sum aggregated in Sheet 2 |

**Table 2 — CREDIT MEMO / RETURNS**
Source file: `Revenue Dashboard_Credit Memo_Returns_IM Australia.xlsx`

| Column | Notes |
|---|---|
| `Index Detail` | |
| `RecordID` | Row identifier |
| `Billing Document` | |
| `Billing Document Item` | |
| `Billing base unit of measure` | |
| `Billing scale quantity in base unit of measure` | Quantity KPI |
| `Channel` | |
| `Company` | |
| `Company Code` | |
| `Customer Name` | |
| `Customer Number` | |
| `Customer Type` | |
| `Document Currency` | |
| `Franchise` | |
| `Industry Text 2` | |
| `Material Description` | |
| `Material Number` | |
| `Order document type description` | |
| `Order manual price change status` | |
| `Order Reason Description` | |
| `Order reference document number` | |
| `Sale Type Description` | |
| `Sales Order Document` | |
| `Sales Order Document Date` | |
| `Sales Order Document Item` | |
| `Sales Org` | |
| `Segment` | |
| `Product hierarchy description 4` | |
| `Transaction Type` | |
| `Billing Date` | |
| `Actual Discount Amount` | Key quantitative KPI |
| `Actual Discount Amount DC` | |
| `Actual Discount Percentage` | Key quantitative KPI |
| `Billing Item Net Value` | Key quantitative KPI |
| `Billing Item Net Value DC` | |
| `Billing List Value` | |
| `Billing List Value DC` | |
| `Billing Net Value` | Key quantitative KPI |
| `Billing Net Value DC` | |
| `Billing Quantity` | Key quantitative KPI |
| `List Price in Base UoM` | |
| `List Price in Base UoM DC` | |
| `Order Item Net Value` | |
| `Order Item Net Value DC` | |

**Table 3 — REVENUE**
Source file: `Revenue Dashboard_Summary_IM Australia.xlsx`
*Same column schema as Credit Memo / Returns table above.*

### 2.7 Known Scope IDs (from `compliance_scope_description`)
```
CONGRESSES__JJ_ORGANIZED_EVENTS · CONTRACT_SALES_AGENTS · DONATIONS · DUE_DILIGENCE
EDUCATIONAL_GRANTS · EQUIPMENT_PLACEMENT · EXPENSE_REPORTS
FEE_FOR_SERVICE_DOCTOR_CONSULTING_FEES · FREE_GOODS · PRODUCT_SAMPLES
PRICING_DISCOUNTS__MARGINS_DISCOUNT_TO_DISTRIBUTOR_PUBLIC_HOSPITAL
PRICE_OVERRIDES · CREDIT_NOTES · PAYMENTS_IN_AP_TO_DISTRIBUTORS__NON_REBATES
REBATES_PAID_TO_DISTRIBUTORS_VIA_ADDITIONAL_INVOICE_DISCOUNT
... (48 total in table)
```

---

## 3. System Architecture

### 3.1 Agent Design
| Agent | Role | LLM | Invocation |
|---|---|---|---|
| **Policy Knowledge Agent** | Retrieves from `hcbi_index` (global policy) and `due_diligence_index` (local policy); understands policies and identifies applicable compliance requirements | No LLM — pure retrieval | Always |
| **Evidence Assessment Agent** | Retrieves from `supporting_docs_index`; reviews supporting evidence provided by respondents against policy requirements | No LLM — pure retrieval | Always |
| **Transactional Data Agent** | NL-to-SQL agent; converts question + response context into a SQL query; executes against 3 transactional Delta tables; returns calculated results to validate quantitative claims in the response | GPT-5 (SQL generation only) | **Conditional** — called by Risk Evaluation Agent only when question/response contains transactional or quantitative context |
| **Risk Evaluation Agent** | Orchestrates all sub-agents; aggregates context; identifies compliance gaps; assesses impact and risk level; calls Transactional Data Agent when needed | GPT-5 + Streaming | Always |
| **Risk Analyzer Agent** | Identifies relevant high-risk scope areas; assigns rationale and risk scores; applies final 10-point validation checklist before output | GPT-5 + Streaming | Always |

**Transactional Data Agent — Design Detail:**
- **Trigger**: Risk Evaluation Agent detects transactional/quantitative intent in the question or response text before invoking
- **Input**: Question text + company response + optional followup Q&A
- **Process**: LLM generates a SQL query → executed against the relevant transactional Delta table(s) → returns calculated result
- **Data**: 3 transactional data files stored as Delta tables (table names and schema to be confirmed)
- **Output**: Structured query result passed back to Risk Evaluation Agent as additional validation context
- **Risk Impact**: If SQL result contradicts the quantitative claim in the response → feeds into risk classification as supporting evidence of potential non-compliance
- **No Vector Index**: This agent does NOT use Databricks Vector Search — direct Delta table SQL only

### 3.2 Pydantic Schemas & LangGraph State

All data flowing through the pipeline is validated via Pydantic models. The `AuditState` TypedDict references these models so every node receives and returns typed, validated data.

```python
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
from typing_extensions import TypedDict
from datetime import datetime


# ── Enums ────────────────────────────────────────────────────────────────

class RiskType(str, Enum):
    POTENTIAL_RISK    = "Potential Risk"
    FURTHER_REVIEW    = "Further Review Required"
    NO_RISK           = "No Risk Identified"

class EvidenceClassification(str, Enum):
    SUPPORTS           = "A - Supports the response"
    PARTIALLY_SUPPORTS = "B - Partially supports the response"
    CONTRADICTS        = "C - Contradicts the response"
    INSUFFICIENT       = "D - Insufficient to test the response"
    NOT_APPLICABLE     = "E - Not applicable"

class DocumentType(str, Enum):
    GLOBAL_POLICY       = "global_policy"
    LOCAL_POLICY        = "local_policy"
    SUPPORTING_DOCUMENT = "supporting_document"
    AUDIT_SKILLS        = "audit_skills"


# ── Input Schema ─────────────────────────────────────────────────────────

class QuestionRow(BaseModel):
    unique_id:            str
    process_review_type:  str
    document_name:        str
    sections:             Optional[str]  = None
    question_no:          int
    question:             str
    company_response:     str
    supporting_document:  Optional[str]  = None
    followup_question:    Optional[str]  = None
    followup_response:    Optional[str]  = None
    synthetic_data:       Optional[str]  = None

    @field_validator("question", "company_response", mode="before")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty or whitespace")
        return v.strip()


# ── Chunk & Retrieval Schemas ─────────────────────────────────────────────

class ChunkMetadata(BaseModel):
    chunk_id:            str
    source_document:     str
    document_type:       DocumentType
    index_name:          str
    section:             str
    subsection:          Optional[str]  = None
    page_number:         Optional[int]  = None
    file_format:         str
    extraction_method:   str
    parent_email:        Optional[str]  = None
    ingestion_timestamp: Optional[datetime] = None

class RetrievedChunk(BaseModel):
    chunk_id:        str
    content:         str
    relevance_score: float = Field(ge=0.0, le=1.0)
    metadata:        ChunkMetadata

    @field_validator("content", mode="before")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Chunk content must not be empty")
        return v.strip()


# ── Conflict Detection Schema ─────────────────────────────────────────────

class ConflictFlag(BaseModel):
    has_conflict:                bool
    conflict_with_question_id:   Optional[str]  = None
    similarity_score:            Optional[float] = Field(default=None, ge=0.0, le=1.0)
    description:                 str             = "None"


# ── Scope Mapping Schema ──────────────────────────────────────────────────

class ScopeAssignment(BaseModel):
    scope_id:         str
    scope_label:      str
    confidence_score: float = Field(ge=0.0, le=1.0)
    is_primary_scope: bool
    note:             Optional[str] = None

    @field_validator("scope_id", mode="before")
    @classmethod
    def scope_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("scope_id must not be empty")
        return v.strip().upper()


# ── Risk Output Schema ────────────────────────────────────────────────────

class SourceDetail(BaseModel):
    source_name:   str
    document_name: str
    section:       Optional[str] = None
    page_number:   Optional[int] = None
    question_id:   Optional[str] = None

class RiskOutput(BaseModel):
    risk_title:               str
    risk_type:                RiskType
    scope_area:               str
    ai_reasoning:             str
    risk_rationale:           str
    evidence_classification:  EvidenceClassification
    evidence_explanation:     str
    questions:                List[str]
    responses:                List[str]
    source_details:           List[SourceDetail]

    @field_validator("risk_title", "ai_reasoning", "risk_rationale", "evidence_explanation", mode="before")
    @classmethod
    def no_empty_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Output field must not be empty")
        return v.strip()


# ── Transactional Data Schema ─────────────────────────────────────────────

class TransactionalQueryResult(BaseModel):
    was_triggered:    bool
    sql_query:        Optional[str]  = None
    query_result:     Optional[str]  = None   # stringified result set
    validation_note:  Optional[str]  = None   # LLM interpretation of result vs response
    table_queried:    Optional[str]  = None

    @field_validator("sql_query", "query_result", mode="before")
    @classmethod
    def strip_if_present(cls, v):
        return v.strip() if v and isinstance(v, str) else v


# ── LangGraph State ───────────────────────────────────────────────────────

class AuditState(TypedDict):
    question_row:              QuestionRow
    retrieval_query:           str
    hcbi_chunks:               List[RetrievedChunk]
    due_diligence_chunks:      List[RetrievedChunk]   # due_diligence_index (current phase)
    supporting_doc_chunks:     List[RetrievedChunk]
    transactional_data_result: TransactionalQueryResult  # None.was_triggered=False if not invoked
    conflict_flag:             ConflictFlag
    aggregated_context:        str
    scope_assignment:          ScopeAssignment
    risk_output:               RiskOutput
```

### 3.3 Processing Flow Per Row
```
Row (question + response + followup Q&A)
  │
  ├─[1] Conflict Detector           — cross-row similarity check via InMemoryStore
  │
  ├─[2] Build retrieval_query       — Q + response + followup_q + followup_a
  │
  ├─[3] asyncio.gather()            — parallel retrieval (always runs):
  │       ├─ Policy Knowledge Agent     → hcbi_index + due_diligence_index
  │       └─ Evidence Assessment Agent  → supporting_docs_index (semantic + targeted)
  │
  ├─[4] Context Aggregator          — apply source hierarchy + rank by relevance score
  │
  ├─[5] Risk Evaluation Agent       — GPT-5, streaming
  │       │   Identifies compliance gaps, assesses impact & risk level
  │       │
  │       └─[5a] Transactional Data Agent  ← CONDITIONAL (called only if transactional/
  │                   │                       quantitative intent detected in Q + response)
  │                   ├─ LLM generates SQL query from question context
  │                   ├─ SQL executed against transactional Delta tables
  │                   └─ Calculated result returned to Risk Evaluation Agent
  │                       as additional validation context
  │
  ├─[6] Risk Analyzer Agent         — GPT-5, streaming
  │       Assigns scope area, rationale, risk scores; final 10-point validation
  │
  └─[7] Output Writer               — write to Delta table
```

### 3.4 Source Priority & Conflict Resolution
| Priority | Source | Rule |
|---|---|---|
| 1 | HCBI Guide (Global Policy) | Always takes precedence |
| 2 | Local Policy (TPI Due Diligence) | Applies when not conflicting with HCBI |
| 3 | Supporting Documents | Evidence only |

**Silence Rule:** If a source does not mention a topic → NOT evidence that activity was not performed.

---

## 4. Document Ingestion Pipeline

### 4.1 Extraction by Format
| Format | Tool(s) |
|---|---|
| `.pdf` image-based | Azure Document Intelligence + Tesseract OCR + Camelot + pdfplumber |
| `.pdf` text-based | pdfplumber + PyMuPDF |
| `.xls` / `.xlsx` | pandas + openpyxl |
| `.ppt` / `.pptx` | python-pptx + Tesseract OCR (for embedded images) |
| `.docx` | python-docx |
| `.html` | BeautifulSoup |
| `.msg` | extract-msg → recursive attachment extraction → re-routed through pipeline |

### 4.2 Chunking Strategy
- **Method**: Section-aware (not fixed-size)
- **Split on**: Document sections / headings → paragraphs
- **Overlap**: 1 paragraph between adjacent chunks
- **Max size**: ~800 tokens
- **Tables**: Treated as single chunks, prefixed with table title

### 4.3 Metadata Per Chunk
```json
{
  "chunk_id": "hcbi_chunk_0042",
  "source_document": "TV-POL-02257_v3.0.pdf",
  "document_type": "global_policy",
  "index": "hcbi_index",
  "section": "Section 3 - Key Principles",
  "subsection": "Third-party Intermediary Risk Management",
  "page_number": 14,
  "file_format": "pdf",
  "extraction_method": "ocr+camelot",
  "parent_email": null
}
```

### 4.4 Vector Indexes
| Index | Source Volume | Embedding Model | Notes |
|---|---|---|---|
| `hcbi_index` | `/global_policies` | text-embedding-3-small | Global policy — applies to all scopes |
| `due_diligence_index` | `/local_policies_tpi` | text-embedding-3-small | Local policy for Due Diligence scope (current phase) |
| `supporting_docs_index` | `/supporting_documents` | text-embedding-3-small | Evidence documents |

**Future index naming convention:** Each scope gets its own local policy index — `{scope_name}_index`
e.g. `expense_reports_index`, `donations_index`, `educational_grants_index` ... (48 total)

---

## 5. Risk & Scope Framework

### 5.1 Decision Categories
| Risk Type | When to Use |
|---|---|
| **Potential Risk** | Evidence confirms specific deviation against applicable policy. All 3 non-compliance criteria must be met. |
| **Further Review Required** | Credible risk indicator exists but evidence is insufficient for definitive conclusion. |
| **No Risk Identified** | Evidence consistent with requirements OR question is background/role information only. |

### 5.2 Non-Compliance Threshold
All three required simultaneously:
1. A specific policy or guidance requirement
2. A relevant supporting evidence document
3. A clear contradiction between the response and the evidence

### 5.3 Evidence Classification (per chunk)
| Code | Classification |
|---|---|
| A | Supports the response |
| B | Partially supports the response |
| C | Contradicts the response |
| D | Is insufficient to test the response |
| E | Is not applicable |

### 5.4 Assessment Rules (from final_prompt.txt)
- Background/role questions → `No Risk Identified` unless specific TPI DD facts present
- Blank/NA/Nil/None responses → do NOT auto-create risk
- "No" → evaluate based on question intent, not treated as generic
- Missing document in retrieved context → do NOT conclude it doesn't exist
- No policy deadline specified → do NOT classify process as delayed

### 5.5 Cross-Question Conflict Detection — Standalone Risk Rule

Conflicting responses within the same questionnaire are a **standalone risk determination pathway**, independent of policy retrieval. This is triggered before the Supervisor Agent and overrides the normal risk flow for the affected rows.

**Trigger conditions (both must be true):**
- Semantic similarity between two question texts > 0.90 (questions are the same or near-identical in meaning)
- Company responses to those questions materially diverge (contradictory in substance)

**Risk determination:**
| Scenario | Risk Type Applied |
|---|---|
| Responses conflict, no additional policy evidence | `Further Review Required` |
| Responses conflict AND policy evidence also shows deviation | `Potential Risk` (policy finding takes precedence) |
| Responses conflict but both align with policy | `Further Review Required` (inconsistency still warrants review) |

**Rules:**
- Both conflicting rows receive **minimum** `Further Review Required` — neither can be `No Risk Identified`
- `Conflict Flag` column is populated with the ID of the conflicting question row on both rows
- AI Reasoning must describe the nature of the conflict: which questions conflict, what the responses state, and why this creates ambiguity
- Conflict detection runs **before** retrieval — the `conflict_flag` is passed into the Supervisor Agent as part of `AuditState`
- Detection uses `InMemoryStore` — question embeddings from all previously processed rows are stored; each new row is compared against the store, then added to it

**What is NOT a conflict:**
- Two questions on different topics that happen to have similar phrasing
- Responses that are both generic (Blank / N/A / Nil) — these are not divergent
- Partial vs complete response — evaluate individually; flag only if substance contradicts

### 5.6 Scope Mapping
1. Embed `question + response + ai_reasoning`
2. Cosine similarity vs all 48 `compliance_scope_description` embeddings
3. Assign top match → `scope_id` + `confidence_score`
4. If confidence < 0.60 → "Scope Unclear — Manual Review Required"
5. If match outside TPI Due Diligence → "Potentially Relevant to Another Scope Area"

---

## 6. Output Specification

### 6.1 Output Table Schema (`compiled_file_scope_risk_assessment`)

The output table contains **all original questionnaire columns first**, followed by the AI-generated columns. No original columns are dropped.

**Part A — Original Questionnaire Columns (carried through as-is)**
| Column | Source Table | Notes |
|---|---|---|
| `unique_id` | Both | Row identifier |
| `process_review_type` | Both | Planning / IDR |
| `document_name` | Both | Source questionnaire file name |
| `sections` | Planning only | Section heading (null for IDR) |
| `question_no` | Both | Question number |
| `question` | Both | Full question text |
| `company_response` | Both | TPI or Management response |
| `supporting_document` | Both | Document name cited by respondent |
| `followup_question` | Both | Follow-up question text (if any) |
| `followup_response` | Both | Follow-up response (if any) |
| `synthetic_data` | Planning only | Synthetic test data flag (if used) |

**Part B — AI-Generated Columns (appended by pipeline)**
| Column | Description |
|---|---|
| `risk_title` | One-liner type label (e.g. "Payments to Unverified TPI") |
| `risk_type` | `Potential Risk` / `Further Review Required` / `No Risk Identified` |
| `risk_score` | **1–10 scale (1 = least risky, 10 = most risky)** |
| `scope_area` | Assigned scope_id from `compliance_scope_description` |
| `scope_confidence` | Cosine similarity score (0.0–1.0) for scope assignment |
| `ai_reasoning` | Objective analysis comparing question + response against retrieved sources |
| `risk_rationale` | Specific evidence-to-policy-requirement connection |
| `evidence_classification` | A / B / C / D / E |
| `evidence_explanation` | Plain-language explanation of the classification |
| `source_evidence` | `Document Name > Section > Page N` for each cited chunk |
| `calculation_evidence` | For data analysis findings — calculation result that validated the risk |
| `conflict_flag` | `None` or ID of the conflicting question row |
| `auditor_review` | **Blank — populated by human auditor** |
| `auditor_notes` | **Blank — populated by human auditor** |

### 6.2 No Document Retrieved
- `Risk Type` = `No Risk Identified`
- `Source Evidence` = "No documents retrieved"
- Rationale = absence of retrieved docs ≠ evidence of non-compliance

### 6.3 Excel Export
- **Sheet 1**: Full risk analysis (color coded: Red=Risk, Yellow=Further Review, Green=No Risk, Orange=Conflict)
- **Sheet 2**: Summary counts by risk type + scope areas covered

---

## 7. Transactional Data Analysis (TransactionalDataAnalysisAgent)

This is a **separate agent** from the NL-to-SQL cross-validation agent (`transactional_data_agent`).
It loads all 3 raw Delta tables + TPI Inventory List + HCC GL Account Map, runs 5 pre-defined
rule-based calculations, and produces its own risk findings in the same output format.

### 7.1 Reference Files Used
| File | Path | Role |
|---|---|---|
| TPI Inventory Final Output | `supporting_documents/TPI_Inventory_Final_Output.xlsx` | TPI name matching — field `Third Party: Legal Entity Name (English)` |
| HCC GL Account Map | `supporting_documents/HCC_GL_Account_Map-11142023.xlsx` | Valid GL Account list for GL validation check |

### 7.2 The 5 Data Analysis Checks

**Check 1 — Payments made to TPIs** *(Risk: Medium)*
- Match: TPI Inventory `Third Party: Legal Entity Name (English)` → Payments `Supplier Name`
- Calculate: total `Document Currency Invoice Amt` per TPI (include negative amounts)
- Sort: descending by total payment amount
- **Risk trigger:** IDR/questionnaire response claims max payments to TPI X but raw data shows TPI Y → Potential Risk
- Display: question + response + raw transactions + total payments for both TPIs

**Check 2 — Credit Notes to TPIs** *(Risk: NA)*
- Match: TPI Inventory → Credit Memo `Customer Name`
- Filter: `Transaction Type` = "Credit Memo"
- Calculate: total `Billing Item Net Value DC` per TPI
- Output: add credit note total column to the payments-per-TPI view

**Check 3 — Sales and Discounts per TPI** *(Risk: NA)*
- Match: TPI Inventory → Revenue `Customer Name`
- Filter: `Transaction Type` = "Sale"
- Calculate per TPI: total `Billing Item Net Value DC` (sales) + total `Actual Discount Amount DC` (discounts)
- Output: add sales total + discount total columns to the payments-per-TPI view

**Check 4 — Percentage of sales to single TPI** *(Risk: Low)*
- Calculate: each TPI's sales as % of total sales across all TPIs
- Sort: descending by percentage
- **Risk trigger:** any single TPI > 50% of total sales → Low Risk
- Display: TPI name + percentage + full revenue data drilldown

**Check 5 — Use of incorrect GL Account** *(Risk: Medium)*
- Load valid GL accounts from `HCC_GL_Account_Map-11142023.xlsx`
- For every row in Payments raw data: check if `GL Account Code` is in the valid GL list
- Add column `valid_gl_account_used` = "Yes" / "No" to payments output
- **Risk trigger:** if any payment to a TPI uses an invalid GL Account → Medium Risk
- Display: flagged transactions alongside the TPI payment sheet

### 7.3 Output Format
Data analysis findings use the **same output columns** as questionnaire findings:
`risk_title`, `risk_type`, `risk_score`, `risk_rationale`, `calculation_evidence`
The `calculation_evidence` column carries the numeric result that supports the finding
(e.g. "TPI X received 67% of total sales; threshold is 50%").

---

## 8. Supervisor Agent Prompt (final_prompt.txt — verbatim)

```
You are an expert auditor specialized in the Third Party Intermediary Due Diligence scope area.

OBJECTIVE
Review the questionnaire responses and determine whether the available evidence indicates a potential risk
in the TPI Due Diligence scope area based only on:
1. HCBI Guide (Global Policy)
2. TPI Due Diligence Policy
3. Supporting documents provided as evidence for the responses

SCOPE LIMITATION
Assess only the TPI Due Diligence scope area.
Do not generate findings for other scope areas. If information is potentially relevant to another scope area,
label it as "Potentially Relevant to Another Scope Area" without making a finding for that area.

SOURCE HIERARCHY
When sources conflict, apply the following priority:
1. HCBI Guide (Global Policy)
2. TPI Due Diligence Policy, provided it does not conflict with the HCBI Guide
3. Supporting documents provided as evidence for the responses
Clearly identify any conflict and apply the higher-priority source.
Do not introduce external knowledge, unsupported assumptions, or information not contained in the permitted sources.

ASSESSMENT RULES
1. Background and compliance-role questions
Do not derive a TPI Due Diligence risk from questions that only concern a person's background, experience,
tenure, general responsibilities, or general compliance role.
Classify these questions as "No Risk Identified" unless the response contains specific facts directly
relevant to TPI Due Diligence.

2. Supporting evidence
Review all supporting-document content provided in the context and identify the exact inconsistency or
evidence gap. For every relevant response, classify the supporting evidence as exactly one of:
a. Supports the response
b. Partially supports the response
c. Contradicts the response
d. Is insufficient to test the response
e. Is not applicable
Do not use vague statements such as "Documentation is inadequate."

3. Generic responses
Do not automatically create a risk from: Blank, N/A, NA, Nil, None, Not applicable, Not available, Unknown
Do not automatically treat "No" as a generic response. Evaluate based on the question's intent.

4. Unsupported assumptions
Base conclusions only on information explicitly provided. If an activity is not mentioned, do not conclude
it was not performed. Do not classify a process as delayed unless policy specifies a deadline that was missed.

EVIDENCE REQUIRED FOR NON-COMPLIANCE
Identify non-compliance only when all three are available:
1. A specific policy or guidance requirement
2. A relevant supporting evidence
3. A clear contradiction between the response's requirement and the evidence

DECISION CATEGORIES
1. Potential Risk — Evidence confirms a specific deviation against applicable policy requirement.
2. Further Review Required — Credible risk indicator exists but evidence is insufficient for definitive conclusion.
3. No Risk Identified — Evidence consistent with requirements OR background/role question.

REQUIRED OUTPUT
Risk Title: [Concise description]
Risk Type: [Potential Risk / Further Review Required / No Risk Identified]
Scope Area: TPI Due Diligence
AI Reasoning: [Objective analysis]
Risk Rationale: [Evidence-to-requirement connection]
Supporting Evidence: [Classification A/B/C/D/E + explanation]
Questions: [Relevant questions]
Responses: [Corresponding responses]
Source Details: [Source name, document name, question ID]

FINAL VALIDATION
Before returning output, verify:
- Every risk linked to specific HCBI or Local Policy requirement
- Every conclusion supported by provided context
- Generic background questions classified as No Risk Identified
- Missing information not treated as evidence of non-performance
- No policy requirement or fact assumed or invented
- Contradiction between response and evidence clearly explained
- Assessment limited to TPI Due Diligence scope area
```

---

## 8. Folder Structure

```
Risk_Analysis/
├── src/
│   │
│   ├── agents/
│   │   ├── risk_evaluation_agent.py      # Risk Evaluation Agent — GPT-5, streaming; identifies compliance gaps & risk level; conditionally calls Transactional Data Agent
│   │   ├── risk_analyzer_agent.py        # Risk Analyzer Agent — GPT-5, scope mapping, rationale & risk scores, final validation
│   │   ├── policy_knowledge_agent.py     # Policy Knowledge Agent — retrieves from hcbi_index + due_diligence_index
│   │   ├── evidence_assessment_agent.py  # Evidence Assessment Agent — retrieves from supporting_docs_index (semantic + targeted)
│   │   └── transactional_data_agent.py   # Transactional Data Agent — NL-to-SQL; queries 3 transactional Delta tables; conditionally invoked
│   │
│   ├── api/
│   │   └── routes/
│   │       └── audit_routes.py        # FastAPI endpoints (future phase)
│   │
│   ├── config/                        # Environment + app configuration
│   │   ├── config.py                  # App-level constants (index names, thresholds, catalog/schema)
│   │   └── settings.py                # Env var loading via pydantic-settings (.env → typed Settings)
│   │
│   ├── database/
│   │   └── repositories/
│   │       ├── questionnaire_repo.py  # Read Planning + IDR Delta tables
│   │       ├── output_repo.py         # Append rows to compiled_file_scope_risk_assessment
│   │       └── scope_repo.py          # Read compliance_scope_description (48 scope embeddings)
│   │
│   ├── infrastructure/
│   │   └── llm/
│   │       ├── azure_openai.py        # GPT-5 AzureChatOpenAI client (LangChain wrapper)
│   │       └── embedder.py            # text-embedding-3-small client (AzureOpenAIEmbeddings)
│   │
│   ├── prompts/
│   │   ├── final_prompt.txt           # Supervisor system prompt (verbatim from final_prompt.txt)
│   │   └── agent_prompts.py           # Sub-agent and context prompts
│   │
│   ├── schemas/
│   │   └── state.py                   # All Pydantic models + AuditState TypedDict
│   │                                  # (RiskType, EvidenceClassification, QuestionRow,
│   │                                  #  RetrievedChunk, ConflictFlag, ScopeAssignment,
│   │                                  #  RiskOutput, SourceDetail, ChunkMetadata)
│   │
│   ├── services/
│   │   ├── ingestion/
│   │   │   ├── document_loader.py     # Universal async loader — routes by file format
│   │   │   ├── chunker.py             # Section-aware chunker (≤800 tokens, 1-para overlap)
│   │   │   ├── vector_store.py        # Databricks Vector Search index create/upsert/query
│   │   │   └── extractors/
│   │   │       ├── pdf_extractor.py   # pdfplumber + Camelot + OCR (image-based PDFs)
│   │   │       ├── excel_extractor.py # pandas + openpyxl (.xls, .xlsx)
│   │   │       ├── ppt_extractor.py   # python-pptx + OCR (.pptx files; note: Audit Skills are PDFs, not PPTs)
│   │   │       ├── word_extractor.py  # python-docx (.docx)
│   │   │       ├── html_extractor.py  # BeautifulSoup (.html)
│   │   │       └── email_extractor.py # extract-msg — recursive attachment extraction (.msg)
│   │   │
│   │   ├── conflict_detector.py       # Cross-row semantic conflict detection (InMemoryStore)
│   │   ├── context_aggregator.py      # Merge + rank 15 chunks, apply HCBI › Local › Docs hierarchy
│   │   └── scope_mapper.py            # Cosine similarity vs 48 scope embeddings → scope_id
│   │
│   ├── tools/
│   │   ├── hcbi_retrieval_tool.py          # LangChain Tool — hcbi_index hybrid search (used by Policy Knowledge Agent)
│   │   ├── policy_retrieval_tool.py        # LangChain Tool — due_diligence_index hybrid search (used by Policy Knowledge Agent)
│   │   └── evidence_retrieval_tool.py      # LangChain Tool — supporting_docs_index hybrid + targeted search (used by Evidence Assessment Agent)
│   │
│   └── utils/
│       ├── text_cleaner.py            # Text normalization, whitespace handling
│       └── metadata.py                # Chunk metadata helpers
│
├── main.py                            # Pipeline entry point + LangGraph StateGraph definition
└── requirements.txt                   # All Python dependencies
```

**Key mapping decisions:**
- `config/` holds all environment + constant config — `settings.py` reads `.env` via pydantic-settings; `config.py` holds named constants (index names, thresholds, catalog path)
- `schemas/` holds all Pydantic models and `AuditState` — single source of truth for types
- `services/ingestion/` contains the full document pipeline (extractors, chunker, vector store)
- `tools/` wraps each retrieval index as a LangChain Tool so agents can call them via tool binding
- `database/repositories/` isolates all Spark / Delta table I/O
- `infrastructure/llm/` isolates all Azure OpenAI client construction
- `StateGraph` is defined in `main.py` — no separate `graph.py`
