# AI Auditor — FCPA Audit API

Enterprise AI audit engine for J&J FCPA / TPI Due-Diligence reviews, exposed as HTTP APIs. It runs on
Azure OpenAI + Databricks (Vector Search, SQL Warehouse, Unity Catalog Volumes & Delta tables) and
ships three audit engines plus an admin layer:

- **Risk Analysis** — classifies & scores questionnaire responses + transactional data → Output A–F.
- **Scoping AI** — turns the Risk Analysis output into per-scope recommendations.
- **Scoping Deck** — generates the CPR Scoping Deck (PPTX) from the prior outputs.
- **Admin** — CRUD for the global knowledge files (policies / FCPA guide), kept in sync with their
  vector indexes.

Everything is served from `main.py` as one FastAPI app; per-run inputs come through the API, and only
secrets/platform values live in `.env`.

---

## Table of Contents
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Authentication (Databricks Service Principal)](#authentication-databricks-service-principal)
- [Environment Variables](#environment-variables)
- [Running](#running)
- [APIs](#apis)
  - [Risk Analysis](#1-risk-analysis)
  - [Scoping AI](#2-scoping-ai)
  - [Scoping Deck](#3-scoping-deck)
  - [Run status & streaming](#run-status--streaming)
  - [Admin — knowledge files](#4-admin--knowledge-files)
  - [Supporting-docs ingest](#5-supporting-docs-ingest)
- [Outputs](#outputs)
- [Project Structure](#project-structure)
- [Docker Deployment](#docker-deployment)

---

## Architecture

```
                         HTTP (FastAPI, served by main.py)
  ┌───────────────┬───────────────┬────────────────┬─────────────────────────┐
  │ Risk Analysis │  Scoping AI   │  Scoping Deck  │  Admin (knowledge files) │
  └───────┬───────┴───────┬───────┴───────┬────────┴────────────┬────────────┘
          │ background job │ background    │ background          │ sync CRUD
          ▼                ▼               ▼                     ▼
   src/pipeline.runner   AI_scope      Scoping_Deck        VectorIndexManager
          │                │               │              (ingest / de-index)
          └────────────────┴───────────────┴──────────────────────┘
                              Azure OpenAI + Databricks
          (Vector Search · SQL Warehouse · UC Volumes · Delta tables · Lakebase obs)
```

- **Run APIs** start a **background job**, return a `run_id`, and report granular status codes (poll or
  SSE-stream). The `RunManager` maps the request onto the engine settings (`_apply_run_env`) — so the
  engine's process-wide `src.config.settings` reflects this run.
- **Admin APIs** perform synchronous file CRUD and keep each global index consistent on upload/delete.
- **Auth** everywhere is a Databricks **service principal (OAuth M2M)** — no PAT.

---

## Prerequisites

- **Python 3.11** (Docker image uses `python:3.11-slim`).
- **Tesseract OCR** — fallback text extraction (`/usr/bin/tesseract` in the image; set `TESSERACT_CMD`
  locally). Azure Document Intelligence is used when its credentials are present.
- **Azure OpenAI** — chat (GPT-5) + embeddings (text-embedding-3-large) deployments.
- **Databricks** — workspace with Vector Search endpoint, a SQL Warehouse, Unity Catalog catalog/schema,
  Volumes, and a **service principal** granted access (see below). Optional Lakebase for observability.

---

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on *nix)
pip install -r requirements.txt
copy .env.example .env          # then fill in secrets (see Environment Variables)
```

---

## Authentication (Databricks Service Principal)

Auth is OAuth M2M via a service principal — set `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`
(no PAT). The SP needs, on the configured catalog/schema/warehouse/volumes:

```sql
GRANT USE CATALOG ON CATALOG <catalog> TO `<client_id>`;
GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA <catalog>.<schema> TO `<client_id>`;
-- Can-use on the SQL warehouse; access to the Vector Search endpoint;
-- READ VOLUME + WRITE VOLUME on the policy/output volumes.
```

Verify everything before deploying:

```bash
python test_spn_databricks.py   # checks auth, warehouse+UC read, table write, and volume read/write
```

---

## Environment Variables

`.env` holds **secrets + platform only**. Everything auditor/run-specific comes through the API.

### Secrets / platform (required)

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_DEPLOYMENT` / `AZURE_OPENAI_API_VERSION` | Chat model |
| `AZURE_EMBEDDING_ENDPOINT` / `AZURE_EMBEDDING_API_KEY` / `AZURE_EMBEDDING_DEPLOYMENT` / `EMBEDDING_DIMENSIONS` | Embeddings |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` / `AZURE_DOCUMENT_INTELLIGENCE_KEY` | OCR (optional; Tesseract fallback) |
| `AZURE_TRANSLATOR_ENDPOINT` / `AZURE_TRANSLATOR_KEY` / `AZURE_TRANSLATOR_REGION` / `TRANSLATION_ENGINE` | Azure AI Translator — set `TRANSLATION_ENGINE=azure_translator` to use it (else Azure OpenAI translates) |
| `AZURE_TRANSLATOR_DOCUMENT_ENDPOINT` / `AZURE_STORAGE_ACCOUNT` / `AZURE_STORAGE_CONTAINER` | Document-translation staging (optional) |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | Azure AD service principal — Key Vault bootstrap + blob/OCR auth |
| `DATABRICKS_HOST` | Workspace URL, e.g. `https://adb-xxxx.azuredatabricks.net` |
| `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` | Service-principal OAuth (replaces the PAT) |
| `DATABRICKS_CATALOG` / `DATABRICKS_SCHEMA_NAME` | Unity Catalog catalog + schema |
| `DATABRICKS_VECTOR_SEARCH_ENDPOINT` / `DATABRICKS_WAREHOUSE_ID` | Vector Search endpoint + SQL warehouse |
| `LAKEBASE_INSTANCE_NAME` / `OBS_ENABLED` | Lakebase observability (optional) |
| `TESSERACT_CMD` | Path to the tesseract binary |
| `HCBI_VOLUME_PATH` / `POLICY_VOLUME_PATH` | Global policy volumes (admin-managed) |
| `API_HOST` / `API_PORT` | API bind address (default `0.0.0.0:8000`) |

> Not in `.env` anymore: `DATABRICKS_TOKEN` (replaced by SP OAuth), and the per-run values
> (`AUDITOR_ID`, `AUDIT_ENGAGEMENT_NUMBERS`, questionnaire tables, ingestion paths, AI-Scope /
> Scoping-Deck inputs) — those are passed via the API.

### Key Vault (optional)

`src/config/config_loader.load_config()` loads `.env` first, then overlays the shared config set from
**Azure Key Vault** (secret names = env-var names with `_`→`-`; the vault value wins). Controls:

| Variable | Default | Meaning |
|---|---|---|
| `KEY_VAULT_ENABLED` | `true` | Set `false` to run from `.env` only (no vault calls) |
| `KEY_VAULT_STRICT` | `false` | `true` makes a vault failure fatal (recommended for prod) |
| `AZURE_KEY_VAULT_NAME` / `AZURE_KEY_VAULT_URL` | `gaa-cpr-risk-analysis-kv-dev` | Target vault |

While the vault is being set up, run with **`KEY_VAULT_ENABLED=false`** and fill the variables above in
`.env`. The vault is reached locally via the `AZURE_TENANT_ID`/`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`
bootstrap credential; on AKS the pod's managed identity is used instead.

---

## Running

```bash
uvicorn main:app --reload        # dev (autoreload)
python main.py                   # serve on API_HOST:API_PORT
```

- Swagger UI: **http://localhost:8000/cpr/risk-analysis/docs** — run every endpoint from the browser.
  (Docs/ReDoc/OpenAPI are mounted under the base path so "Try it out" hits the mounted routes — plain
  `/docs` returns 404 by design. ReDoc: `…/cpr/risk-analysis/redoc`.)
- Health: `GET /health` · Root: `GET /` (returns the docs link)

---

## APIs

Base prefix: **`/cpr/risk-analysis`**. Each run API starts a background job and returns `202 {run_id, status_url}`.
Secrets/platform come from `.env`; the fields below are the per-run inputs.

### 1. Risk Analysis
`POST /cpr/risk-analysis/risk-analysis/runs` — **all inputs mandatory except SI/Legal/past-audit**.
```json
{
  "auditor_id": "Sam_1",
  "engagement_numbers": "1000,1001",
  "questionnaire": { "idr": ["idr_sam_1"], "planning": ["consolidated_sam_1"] },
  "supporting_docs_path": "/Volumes/.../auditors_supporting_docs/Sam_1",
  "si_history_path": "/Volumes/.../si-history/Sam_1",      // optional
  "legal_path": "/Volumes/.../legal/Sam_1",                // optional
  "past_audit_path": "/Volumes/.../past-audit-data/Sam_1", // optional
  "force_ingest": false
}
```
Questionnaire tables accept **multiple** tables per type; columns (question / response / one-or-many
follow-ups) are mapped by an LLM with a constant-name fallback.

### 2. Scoping AI
`POST /cpr/risk-analysis/scoping-ai/runs` — reads Risk Analysis **Output C**.
```json
{
  "auditor_id": "Sam_1",
  "source": "db_table",                                     // or "excel"
  "path": "nbod_gaagd.risk_analysis_output.AI_Risk_Summary_1000_1001_copy",
  "output_e_path": "nbod_gaagd.risk_analysis_output.Summary_Past_Reviews_1000_1001_copy", // db_table only; omit → no past-audit risks
  "output_f_path": "nbod_gaagd.risk_analysis_output.Scope_Transaction_Summary_1000_1001"  // db_table only; omit → auto-derived from the Output C table
}
```
With `source: "excel"`, pass the Output C workbook as `path` — Output E/F are read from that same
workbook and the `output_*` paths are ignored. Scoping AI also pushes its result to the `AI_Scope`
Delta table (+ `_copy`), so Scoping Deck can read it as a `db_table`.

### 3. Scoping Deck
`POST /cpr/risk-analysis/scoping-deck/runs` — identity + scope period + the prior outputs.
```json
{
  "auditor_id": "Sam_1",
  "entity_name": "IM Australia",
  "scope_period_start": "2024-01-01",
  "scope_period_end": "2024-12-31",
  "country": "Australia", "iso3": "AUS", "mrc_number": "MRC1234",   // optional
  "source": "db_table",                                            // or "excel" (governs both reads)
  "risk_scope_path":  "nbod_gaagd.risk_analysis_output.AI_Scope_1000_1001_copy",
  "past_review_path": "nbod_gaagd.risk_analysis_output.Summary_Past_Reviews_1000_1001_copy",
  "output_f_path":    "nbod_gaagd.risk_analysis_output.Scope_Transaction_Summary_1000_1001"  // db_table only
}
```
With `source: "excel"`, pass the AI-Scope `.xlsx` and the FCPA audit `.xlsx` instead (Output F is read
from the same workbook as Output E — omit `output_f_path`).

### Run status & streaming
| Method | Path | Purpose |
|---|---|---|
| GET | `/cpr/risk-analysis/<api>/runs/{run_id}` | Current status (`status_code`, label, progress, result/error) |
| GET | `/cpr/risk-analysis/<api>/runs/{run_id}/stream` | **SSE** stream of status changes until terminal |
| GET | `/cpr/risk-analysis/<api>/runs` | List runs for the API |

`state ∈ pending | running | succeeded | failed`. Only one run may be active at a time (409 otherwise).

On success, `result` carries the output locations: `output_path` / `deck_path` (the generated file),
`volume_path` (its UC Volume path), and `db_tables` — a map of the Delta tables the run wrote (Risk
Analysis: Output A/C/E/F with `_copy` for A & C; Scoping AI: `ai_scope` + `_copy`; Scoping Deck: empty,
it writes no tables). `db_tables` is empty when no Databricks warehouse is configured.

### 4. Admin — knowledge files
Manage the global sources and keep each vector index in sync. `<resource>` ∈
`global-policies` | `global-scope-policies` | `fcpa-guide`.

| Method | Path | Behaviour |
|---|---|---|
| GET    | `/cpr/risk-analysis/admin/<resource>/files` | List files in the source volume |
| GET    | `/cpr/risk-analysis/admin/<resource>/files/{name}` | Download a file |
| POST   | `/cpr/risk-analysis/admin/<resource>/files` | **Upload** (multipart `file`) → write + ingest into the index (replaces an existing file's chunks) |
| DELETE | `/cpr/risk-analysis/admin/<resource>/files/{name}` | **Delete** → remove file + delete its chunks from the index |

The FCPA guide is a single workbook: upload rebuilds its `scope_policy` index. File→index sync uses
`VectorIndexManager.delete_by_document_name`.

### 5. Supporting-docs ingest
Upload a single supporting document for one auditor and ingest it on demand (e.g. a file that arrived
after the run, or one that failed mid-ingest) — no full re-run needed.

| Method | Path | Behaviour |
|---|---|---|
| POST | `/cpr/risk-analysis/supporting-docs/files` | multipart (`auditor_id` form field + `file`) |

Writes the file to the auditor's supporting-docs volume folder (`<base>/<auditor_id>/<name>`) and
(re)ingests it into the shared `supporting` index tagged with that `auditor_id` — replacing **this
auditor's** chunks for a same-named file (never another auditor's). Returns
`{ auditor_id, file, status, chunks_indexed, chunks_removed, path }`.

---

## Outputs

**Delta tables** (editable `_copy` variants) are written to the `risk_analysis_output` schema (unchanged
naming: `AI_Observations`, `AI_Risk_Summary`, `Summary_Past_Reviews`, `Scope_Transaction_Summary`,
`AI_Scope`, suffixed `_<engagement>` / `_copy`).

**Output files** are uploaded to UC Volumes under `risk_analysis_output`:
| Engine | Volume | Naming |
|---|---|---|
| Risk Analysis | `/risk_analysis_output` | `<engagement>_output_<timestamp>.xlsx` (kept per run) |
| Scoping AI | `/ai_scope_outputs` | `<engagement>_aiscope_<timestamp>.xlsx` (kept per run) |
| Scoping Deck | `/scoping_deck` | deck's own name (**overwritten** — latest for the entity) |

Dates render as `Month D, YYYY` across all outputs. Observability (token/cost/latency) is recorded to
Lakebase when `OBS_ENABLED`.

---

## Project Structure

```
main.py                     entrypoint — builds + serves the FastAPI app (uvicorn main:app)
app/
  application.py            FastAPI app factory
  core/        config · exceptions (typed AppError + handlers) · run_store
  schemas/     runs (per-API request models) · status_codes · admin
  services/    run_manager (orchestration + request→settings) · risk_analysis · scoping_ai ·
               scoping_deck · admin_files · supporting_ingest · progress
  api/         run_router (per-API factory) · admin_router · supporting_router · router · health
src/
  pipeline/    runner (Risk Analysis engine) · supervisor · output_upload · ...
  agents/ prompts/ repositories/ vector_store/ ingestion/ output/ scoring/
  config/      settings (env) · databricks_auth (SP OAuth) · config_loader (Key Vault) · constants
  observability/  Lakebase run tracing
AI_scope/                   Scoping AI engine
Scoping_Deck/               Scoping Deck engine
scripts/                    dev utilities
Dockerfile · requirements.txt · .env
```

---

## Docker Deployment

```bash
docker build -t ai-auditor .
docker run --env-file .env -p 8000:8000 ai-auditor
# → http://localhost:8000/cpr/risk-analysis/docs
```

The image installs Tesseract + deps and serves the app with `uvicorn main:app` as a **single worker**
(`CMD` honours `API_HOST`/`API_PORT`/`API_WORKERS`), with a stdlib `HEALTHCHECK` on `GET /`. Inject all
secrets at runtime via `--env-file`/`-e` — **never bake `.env` into the image**. Keep `API_WORKERS=1`:
the in-memory run store is process-local, so multiple workers would split run state — back it with Redis
/ the observability DB before scaling out. Don't use `--reload` in production.
