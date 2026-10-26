# AI Auditor — Tech Debt & Technology Stack

**Project:** AI Auditor — JNJ TPI Due Diligence
**Platform:** Databricks (Unity Catalog) on Azure
**Language:** Python
**Code Format:** Python files with folder structure on Databricks

---

## Core AI & Orchestration

| Technology | Version | Purpose | Why Chosen |
|---|---|---|---|
| **LangChain** | `>=0.2.0` | Chain construction, prompt templates, tool binding, Azure OpenAI wrappers | Industry standard; native Azure OpenAI integration; clean agent tool interface |
| **LangGraph** | `>=0.1.0` | Multi-agent StateGraph, supervisor pattern, parallel node execution | Built for multi-agent workflows; async-native; integrates natively with LangChain |
| **LangGraph InMemoryStore** | Built-in | Agent memory scoped to pipeline run; stores question embeddings for cross-row conflict detection | No external memory DB needed; auto-cleared after each batch run |
| **langchain-openai** | `>=0.1.0` | LangChain wrapper for Azure OpenAI (LLM + Embeddings) | Official integration; supports async + streaming |

---

## LLM & Embeddings

| Technology | Model / Endpoint | Purpose |
|---|---|---|
| **Azure OpenAI GPT-5** | `genaiapimna.jnj.com/openai-gpt-5/openai/deployments/gpt-5/chat/completions?api-version=2024-10-21` | Supervisor Agent — risk analysis, evidence classification, scope analysis, final validation |
| **Azure OpenAI text-embedding-3-small** | JNJ internal Azure OpenAI gateway | Chunk embeddings for vector indexing; query embeddings at retrieval; scope cosine similarity matching |
| **Streaming** | Enabled via `.astream()` | Token-by-token output for faster async pipeline response |

---

## Vector Database

| Technology | Version | Purpose | Why Chosen |
|---|---|---|---|
| **Databricks Vector Search** | Latest (via Databricks SDK) | Hosts 3 vector indexes: `hcbi_index`, `due_diligence_index`, `supporting_docs_index` | Already part of Databricks ecosystem; no additional infra; Delta Sync keeps vectors in sync automatically |
| **databricks-sdk** | `>=0.28.0` | Databricks platform client — Vector Search, Delta table read/write, secrets | Official SDK |
| **databricks-vectorsearch** | `>=0.1.0` | Python client for index creation, upsert, query operations | Official Vector Search client |

---

## Document Processing — Universal Loader

| Technology | Version | Format Handled | Purpose |
|---|---|---|---|
| **pdfplumber** | `>=0.10.0` | `.pdf` (text-based) | Clean text + layout-aware table extraction |
| **PyMuPDF (fitz)** | `>=1.24.0` | `.pdf` (all) | Fallback text extraction, page rendering, metadata |
| **Camelot-py** | `>=0.11.0` | `.pdf` (tables) | Structured table extraction — lattice and stream modes |
| **pytesseract** | `>=0.3.10` | Image-based PDFs, embedded images | OCR text recognition from scanned/image content |
| **Azure Document Intelligence** | `azure-ai-formrecognizer>=3.3.0` | Complex image-based PDFs (HCBI) | High-accuracy structured extraction for charts/tables |
| **python-pptx** | `>=0.6.23` | `.ppt`, `.pptx` | Slide title, body text, notes extraction |
| **python-docx** | `>=1.1.0` | `.docx` | Paragraph and heading extraction |
| **pandas** | `>=2.2.0` | `.xls`, `.xlsx` | Tabular data extraction with sheet iteration |
| **openpyxl** | `>=3.1.0` | `.xlsx` | Excel read engine backend for pandas |
| **BeautifulSoup4** | `>=4.12.0` | `.html` | Tag-aware text extraction, section hierarchy detection |
| **extract-msg** | `>=0.48.0` | `.msg` | Email body + recursive nested attachment extraction |
| **Pillow** | `>=10.3.0` | Images inside docs | Image pre-processing before OCR pipeline |
| **OpenCV** | `>=4.9.0` | Images inside docs | Required by Camelot for table boundary detection |

---

## Data Layer

| Technology | Purpose |
|---|---|
| **Databricks Delta Tables** (Unity Catalog) | All structured data — questionnaire input, output results, scope reference, audit skills context |
| **Apache Spark** (via Databricks) | Reading and writing Delta tables |
| **Delta Lake** | ACID transactions on output writes; supports row-by-row incremental append |

---

## Async & Performance

| Technology | Purpose |
|---|---|
| **asyncio** | Python native async runtime — all pipeline nodes are async |
| **aiofiles** | Async file I/O for document loading |
| **asyncio.gather()** | Parallel execution of 3 retrieval sub-agents per question row |
| **LangChain async** | `.ainvoke()` and `.astream()` for non-blocking LLM and chain calls |

---

## Configuration & Secrets

| Technology | Purpose |
|---|---|
| **python-dotenv** | Load `.env` file into environment variables |
| **settings.py** | Central typed config class — reads all env vars, exposes to all modules |
| **Databricks Secret Scope** | Stores Azure OpenAI API keys when running in Databricks |
| **pydantic-settings** | Config validation and type safety |

---

## Environment Variables (`.env`)

```env
# ── Azure OpenAI — LLM ─────────────────────────────────────────
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=https://genaiapimna.jnj.com/openai-gpt-5/openai/deployments/gpt-5
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-5

# ── Azure OpenAI — Embeddings ──────────────────────────────────
AZURE_EMBEDDING_API_KEY=
AZURE_EMBEDDING_ENDPOINT=
AZURE_EMBEDDING_DEPLOYMENT_NAME=text-embedding-3-small
AZURE_EMBEDDING_API_VERSION=2024-02-01

# ── Databricks ─────────────────────────────────────────────────
DATABRICKS_HOST=
DATABRICKS_TOKEN=
DATABRICKS_CATALOG=cat_cbt_gaa_brz_dev
DATABRICKS_SCHEMA=default

# ── Vector Search Indexes ──────────────────────────────────────
HCBI_INDEX_NAME=hcbi_index
DUE_DILIGENCE_INDEX_NAME=due_diligence_index
# Future: one index per scope — {SCOPE_NAME}_INDEX_NAME={scope_name}_index
SUPPORTING_DOCS_INDEX_NAME=supporting_docs_index

# ── Pipeline Configuration ─────────────────────────────────────
TOP_K_RETRIEVAL=5
CONFLICT_SIMILARITY_THRESHOLD=0.90
SCOPE_CONFIDENCE_THRESHOLD=0.60
MAX_CHUNK_TOKENS=800
CHUNK_OVERLAP_PARAGRAPHS=1
STREAMING_ENABLED=true
```

---

## Full Python Requirements

```txt
# Core AI / Orchestration
langchain>=0.2.0
langgraph>=0.1.0
langchain-openai>=0.1.0
langchain-community>=0.2.0
openai>=1.30.0

# Document Processing
pdfplumber>=0.10.0
camelot-py[cv]>=0.11.0
PyMuPDF>=1.24.0
pytesseract>=0.3.10
python-pptx>=0.6.23
python-docx>=1.1.0
beautifulsoup4>=4.12.0
extract-msg>=0.48.0
openpyxl>=3.1.0
pandas>=2.2.0
Pillow>=10.3.0
opencv-python>=4.9.0
azure-ai-formrecognizer>=3.3.0

# Databricks
databricks-sdk>=0.28.0
databricks-vectorsearch>=0.1.0

# Configuration & Validation
python-dotenv>=1.0.0
pydantic>=2.7.0
pydantic-settings>=2.3.0
# Pydantic models used for: QuestionRow, ChunkMetadata, RetrievedChunk,
# ConflictFlag, ScopeAssignment, SourceDetail, RiskOutput, AuditState

# Async
aiofiles>=23.2.1

# Utilities
numpy>=1.26.0
```

---

## Tech Decisions Log

| Decision | Rationale |
|---|---|
| **LangGraph over CrewAI / AutoGen** | Native async; deterministic state flow; first-class streaming; tight LangChain integration |
| **Databricks Vector Search over Pinecone/Chroma** | Already in Databricks ecosystem; no extra infra; Delta Sync auto-keeps index in sync |
| **text-embedding-3-small over ada-002** | Higher quality embeddings; lower cost; better semantic accuracy |
| **Streaming ON** | Reduces perceived latency in async row-by-row processing |
| **InMemoryStore for conflict detection** | Lightweight; scoped to run; no persistence needed between runs |
| **Section-aware chunking over fixed-size** | Policy docs have natural section boundaries; preserving them improves retrieval accuracy |
| **Camelot for PDF tables** | Best open-source PDF table extractor; handles both lattice and stream tables |
| **extract-msg recursive** | `.msg` files may contain nested attachments; all must be ingested as evidence |
| **settings.py + .env** | Single source of truth for config; easy swap to Databricks secret scope when deploying |
| **No FastAPI now** | Pipeline runs as Python script in Databricks; FastAPI added in future phase |
| **No external DB** | Databricks Delta tables provide ACID storage; no duplication of infrastructure |
| **3 separate vector indexes** | Each agent retrieves from its own source type; avoids cross-contamination |
| **Master Audit Skills as system context** | These are reasoning guidelines, not retrieval targets; loading as context more appropriate than indexing |

---

## Known Constraints & Future Tech Debt

| Item | Type | Notes |
|---|---|---|
| `text-embedding-3-small` dimension = 1536 | Constraint | Fixed by Azure OpenAI model; index schema must match |
| HCBI PDF is image-based | Technical complexity | Requires Azure Document Intelligence + OCR; extraction quality depends on scan quality |
| `.msg` recursive depth | Risk | Deep nesting (email → attachment → email → attachment) needs depth limit to avoid infinite loops |
| GPT-5 context window | Constraint | Aggregated context from 3 agents (5 chunks each = 15 chunks) must fit within context; chunk size tuned accordingly |
| Databricks Vector Search sync lag | Constraint | Delta Sync index has a sync delay; pipeline must wait for index sync after ingestion before querying |
| 48 scope agents | Future | Currently 1 scope (Due Diligence) has local policy; 47 remaining scopes need dedicated local policy agents |
| FastAPI layer | Future | To be added for external access to pipeline |
| Transactional data | Future | Raw transactional data analysis not in current phase |
| Human-in-the-loop | Future | `Auditor Review` + `Auditor Notes` columns are placeholders; integration with review workflow TBD |
