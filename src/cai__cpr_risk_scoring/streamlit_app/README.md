# AI Auditor — Observability Dashboard (Streamlit)

A Langfuse-style dashboard over the project's **Lakebase** observability tables, plus two
**semantic-comparison** tabs that diff an output table against its chat-edited `_copy`.

It **reuses the main app's environment** — same `.env` / Key Vault, same `Settings`, same Databricks
service-principal credentials (`streamlit_app/config.py` puts the project root on `sys.path` and calls
`load_config()`), so there's nothing new to configure.

## Run
```bash
# from the project root, with the main app's venv active:
pip install -r streamlit_app/requirements.txt      # main requirements.txt must already be installed
streamlit run streamlit_app/app.py
```
Needs `OBS_ENABLED` to have been true for runs to appear, a reachable `LAKEBASE_INSTANCE_NAME`, and
(for the semantic tabs) `DATABRICKS_WAREHOUSE_ID` + the output schema.

## Navigation
| Tab | Source | Shows |
|---|---|---|
| 📊 Overview | `public.obs_run` | Fleet KPIs — cost, tokens, LLM calls, completion, per-run cost/token trends, severity mix |
| 🧵 Run detail | `obs_question` / `obs_llm_call` / `obs_finding` / `obs_ingestion` | Drill into one run (trace-style) |
| 🤖 LLM analytics | `obs_llm_call` | Cost / tokens / latency / errors by stage · agent · model · prompt · scope |
| ⚠️ Findings | `obs_finding` | Severity distribution and scope-area × severity |
| 📥 Ingestion | `obs_ingestion` | Files by status, chunks by index, token/cost totals |
| 🔬 Semantic — Risk Analysis | Delta `AI_Risk_Summary` vs `_copy` | Row-by-row semantic change + field-level diffs |
| 🔬 Semantic — AI Scope | Delta `AI_Scope` vs `_copy` | Row-by-row semantic change + field-level diffs |

## Semantic comparison
Enter an **engagement number** (auto-builds `catalog.schema.<base>_<eng>` and its `_copy`) or two
**fully-qualified** table names. Rows are aligned by a key column; each changed row is embedded with the
app's Azure embedding model and scored by cosine similarity, so you see **how much** the chat edits
changed each row and **exactly which fields** changed (before/after + a unified text diff). Identical
rows cost no embedding calls.

## Layout
```
streamlit_app/
  app.py                  entrypoint + sidebar navigation
  config.py               bootstrap: sys.path, settings (env + Key Vault), cached SQL engine
  data/
    lakebase_repo.py      read-only queries over the obs_* tables → DataFrames (cached)
    delta_repo.py         read Delta output tables via the SQL warehouse
  services/
    semantic_diff.py      embedding-based original-vs-copy comparison + field/line diffs
  views/
    overview.py · runs.py · llm_calls.py · findings.py · ingestion.py · semantic.py
```
Nothing here writes — it's read-only over Lakebase and the Delta warehouse.
