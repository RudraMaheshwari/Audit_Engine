# Observability UI

A dynamic web dashboard for the AI Auditor — a **Python/FastAPI backend** (data + similarity + LLM
compute, reusing the main app's Lakebase / Databricks / embeddings / LLM) and a **Next.js frontend**
(the eye-catching UI). Replaces the old Streamlit app.

Three dashboard lenses (the internal names are intentionally **not** shown in the UI) + a change-review:

| Nav label | Lens | What it answers |
|---|---|---|
| **Operations** | technical | runs, cost, tokens, LLM calls, latency, ingestion — pipeline health |
| **Risk Overview** | audit outcomes | of all runs, how many are high/medium/low risk; findings mix; coverage; by auditor |
| **Executive** | leadership / ROI | audits delivered, high-risk caught, AI spend, cost efficiency, estimated hours saved & ROI |
| **Change Review** | semantic diff | what reviewers changed in the `_copy`, scored by meaning and **LLM-summarised for retraining** |

```
observability_ui/
  backend/            FastAPI — Python functions exposed as APIs (read-only)
    bootstrap.py      sys.path + Settings (.env + Key Vault), reused from the main app
    lakebase_queries  obs_* queries (TTL-cached)
    levels.py         technical() · user_level() · leadership()  (pure functions)
    semantic.py       delta read + embedding cosine + per-row LLM change summary
    main.py           routes + CORS
  frontend/           Next.js 14 (App Router, TypeScript, Tailwind, recharts, next-themes, SWR)
    app/              page.tsx (Operations) · risk · executive · change-review
    components/       nav · theme-toggle · ui · charts · providers
    lib/api.ts        typed fetch + SWR hooks
```

## Run

**1. Backend** (main venv active, main `requirements.txt` installed). Run from **inside
`observability_ui/`** so `backend` is importable:
```bash
pip install -r observability_ui/backend/requirements.txt      # once (from the project root)
cd observability_ui
uvicorn backend.main:app --reload --port 8600
# or from the project root:  uvicorn backend.main:app --app-dir observability_ui --reload --port 8600
# health: http://localhost:8600/api/health
```
Needs the same env as the main app (Lakebase `LAKEBASE_INSTANCE_NAME` + Databricks SP creds; `OBS_ENABLED`
runs populate the data; `DATABRICKS_WAREHOUSE_ID` + output schema for Change Review).

**2. Frontend:**
```bash
cd observability_ui/frontend
cp .env.local.example .env.local      # point NEXT_PUBLIC_API_BASE at the backend
npm install
npm run dev                            # http://localhost:3000
```

## Executive ROI assumptions (defensible, overridable)
`est_hours_saved = runs × OBS_MANUAL_HOURS_PER_AUDIT` (default 40); `est_value = hours ×
OBS_ANALYST_HOURLY_USD` (default 120); `ROI% = (est_value − AI spend) ÷ AI spend`. Set those two env
vars on the backend to match your blended analyst rate.

## Dynamic / theming
- **Dark & light** via `next-themes` + CSS variables — instant switch, charts re-theme automatically.
- **Live data** via SWR (no page reloads); the nav **Refresh** clears the backend cache and revalidates.
- Everything is driven by the API responses — no hard-coded figures.

## Notes
- Backend is **read-only**. Change Review's LLM summaries are best-effort (degrade gracefully if the
  embedding/LLM provider is unavailable — you still get the field-level before/after diffs).
- The old `streamlit_app/` can be removed once you've migrated.
