# Risk Analysis — Current vs. Target Flow

**Purpose:** Compare what the AI Auditor does **today** (question-centric) against what the
reviewer spec (`RiskAnalysisSteps`) requires (**risk-centric, in totality**), and identify exactly
what must change, what is reused, and what is committed for **Oct 5** vs. **Phase 2**.

**Date:** 2026-09-15
**Deadline reference:** Oct 5, 2026 (commit)

---

## 1. The core shift in one line

| | Anchor / unit of analysis | Mapping |
|---|---|---|
| **Current (have)** | one **Question & Response** | `Q&R (1) → Risk Type (many) → Scope Area (many)` |
| **Target (need)** | one **Risk** | `Risk Type (1) → Scope Area (1) → Q&R (many) + Transactions (many) + Docs (many)` |

The same underlying issue currently repeats across many question rows. The target **clusters all
findings — from responses AND transactional data — into unique risks**, each mapped to one scope
area, evidence clubbed together, with a drill-down to the source items.

---

## 2. CURRENT FLOW (what we have)

```
INPUTS
  • Global policies index (HCBI + scope-area)          [vector]
  • Supporting docs index (per auditor)                [vector]
  • Questionnaire tables (IDR / consolidated / ...)    [Delta]
  • Transactional tables (payments/credit/revenue)     [Delta]
        │
        ▼
PER-QUESTION PIPELINE  (8-agent LangGraph, one run per Q&R)
  scope_identifier → hcbi_policy → policy_knowledge → supporting_docs
  → risk_evaluation → risk_classifier → risk_writer
        │
        ▼
PER-QUESTION OUTPUT  (one Excel row per question)
  S.No | Questionnaire File | Question | Response | Source Details |
  Risk Description | Rationale | Risk Category | Risk Type | Scope Area(s)
        │
        ▼
PHASE 3 (separate) — transactional_data_analysis_agent
  templated SQL over fixed tables → appended as extra rows
        │
        ▼
SCOPE SUMMARY sheet (appended)
```

**Characteristics**
- Each question independently produces risk(s) → **duplication** of the same risk across rows.
- Each question is **scope-tagged** individually.
- Transactional analysis runs **after** questions, as a **separate** sheet (not merged into risks).
- Risk Category from **R1–R6 bifurcation + internal severity score** (not an Impact×Likelihood matrix).
- Deliverable **is** the per-question Excel.

---

## 3. TARGET FLOW (what needs updating)

```
LAYER 0 — INPUTS (gather everything FIRST, in totality)
  • Global policies (prereq FCPA + all scope context)              [vector]   (have)
  • Supporting docs, read WITH the response they're attached to    [vector]   (change: linkage)
  • Questionnaire Q&R + follow-ups                                 [Delta]    (have)
  • Databricks transactional data — 7 dashboards                   [Delta]    (expand)
  • Transactional data INSIDE supporting docs (variable columns)   [raw file] (NEW lane)
  • Past audit reports (from supp docs + manual upload)            [vector]   (NEW index)
  • SI History + Legal inputs (auditor-entered)                    [vector]   (NEW index)
  • TPI Inventory (uploaded as a supporting doc)                   [file]     (change: source)
        │
        ▼
LAYER 1 — ANALYZE TOGETHER (not separately — step 7a)
  • Transactional risks (Databricks + in-doc) computed EARLY and fed into
    the reading of responses/docs
  • Per-question analysis engine (REUSED) produces raw findings
  • Absence of evidence ≠ risk (Gate C — already in place, step 7b)
  • Do NOT scope-tag each Q&R (step 7c)
        │
        ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  YOUR CURRENT OUTPUT FILE  =  per-question findings (raw pool)            ║
║  one row per Q&R (Risk Desc | Rationale | Category | Risk Type | Scope)   ║
║  ROLE: no longer the deliverable — INTERNAL intermediate that            ║
║        aggregation (Layer 2) runs ON TOP OF                               ║
╚══════════════════════════════════════════════════════════════════════════╝
        │
        │  + Databricks transactional risks (7 dashboards)   [step 5]
        │  + in-doc table transactional risks (raw files)    [step 6c]
        │      (merged into the SAME pool BEFORE aggregation)
        ▼
LAYER 2 — AGGREGATE INTO RISKS (step 8)  ── runs on top of the current output pool
  (a) cluster similar findings → UNIQUE Risk Types
  (b) map each Risk Type → ONE Scope Area
  (c) description CLUBS every source item (each response + each transaction)
  (d) score category via RISK MATRIX = Impact × Likelihood
         15–25 High | 10–14 Medium | 1–9 Low
  (e) recurrence check vs Past Audit / SI History / Legal
        │
        ▼
AI RISK ANALYSIS PAGE — one line per unique Risk Type
  description | rationale | category   [+ View Details → source Q&R / transactions]
        │  (+ SI History risks, + Legal risks as their own lines)
        ▼
RISK SUMMARY PAGE (step 9) — grouped by Scope Area, ordered High→Med→Low (read-only)
        │
        ▼  [ Generate Scope ]
        ▼
SCOPE PAGE (step 10) — per-scope AI summary; notes recurrence;
                       checkbox to exclude scope areas
```

---

## 3A. WHERE YOUR CURRENT OUTPUT SITS IN THE TARGET FLOW

Your current per-question Excel does **not** get thrown away — it is **demoted from the deliverable
to an internal intermediate**. It becomes the *raw findings pool* that the new aggregation layer
consumes. Nothing about the per-question analysis is wasted; it just stops being the final artifact.

```
        ┌──────────────────────────────────────────────────────────────┐
        │  CURRENT PIPELINE (kept as-is, runs as Layer 1)              │
        │  8-agent per-question analysis                               │
        └──────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
   ╔══════════════════════════════════════════════════════════════════════╗
   ║  YOUR CURRENT OUTPUT  =  per-question findings                        ║
   ║  S.No | Questionnaire File | Question | Response | Source Details |   ║
   ║  Risk Description | Rationale | Risk Category | Risk Type | Scope     ║
   ║                                                                      ║
   ║  ROLE CHANGES:  deliverable  ──►  INTERNAL intermediate (raw pool)    ║
   ║  • one entry per Q&R (gap / evidence verdict / candidate risk)        ║
   ║  • per-question Scope tagging is DROPPED here (moves to Layer 2)      ║
   ╚══════════════════════════════════════════════════════════════════════╝
                                   │
        merged (BEFORE aggregation) with, in the SAME pool:
        ┌───────────────────────────────┬───────────────────────────────┐
        │ Databricks transactional risks │ In-doc table transactional     │
        │ (7 dashboards)        [step 5] │ risks (raw files)     [step 6c]│
        └───────────────────────────────┴───────────────────────────────┘
                                   │
                                   ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  STEP 8 — AGGREGATE INTO UNIQUE RISKS                                  │
   │  cluster (responses + transactions) → unique Risk Type → 1 Scope Area │
   │  club all evidence · score via Risk Matrix · recurrence check         │
   └──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  AI RISK ANALYSIS PAGE  (one line per Risk Type)                       │
   │  [View Details]  ──►  shows the CURRENT-OUTPUT rows + transactions     │
   │                       that produced this risk                          │
   └──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
   RISK SUMMARY (step 9)  ──►  [Generate Scope]  ──►  SCOPE PAGE (step 10)
```

**Key point:** the *View Details* drill-down on each risk is literally your **current output rows**,
re-surfaced as the supporting evidence for that risk — so the per-question detail is preserved, just
no longer the top-level view.

| Your current output | Role today | Role in target |
|---|---|---|
| Per-question rows | The deliverable | Internal raw findings pool (Layer 1) |
| Per-row Scope Area | Set per question | Removed here; assigned once per risk (Layer 2) |
| Risk Description / Rationale | Final text | Merged/clubbed into the unique risk's description |
| Risk Category | R1–R6 + severity | Recomputed via Impact × Likelihood matrix |
| The whole row | Shown to user | Shown behind **View Details** of its parent risk |

---

## 4. COMPONENT-LEVEL GAP ANALYSIS

| # | Component | Current (have) | Target (need) | Effort |
|---|---|---|---|---|
| 1 | **Analysis unit** | per-question | per-**risk** (aggregation layer on top) | **High** |
| 2 | **Aggregation / clustering** | none | cluster findings → unique Risk Types, evidence clubbed, drill-down | **High** |
| 3 | **Scope tagging** | per-question | removed at Q level; assigned at aggregation (one per risk) | Low |
| 4 | **Risk Category** | R1–R6 + severity score | **Impact × Likelihood matrix** (needs Likelihood defs) | Low* |
| 5 | **Transactional (Databricks)** | Phase-3, separate, fixed SQL | 7 dashboards, run **early**, feed responses, bounded (code aggregates → LLM summary) | **High** |
| 6 | **Transactional (in-doc tables)** | not usable (chunked in vector store) | structured lane over **raw files**, auto-detect tables, compute aggregates | **High** |
| 7 | **Supporting-doc linkage** | semantic by auditor folder; filename in text | read WITH the response it's uploaded against (structural link) | Medium |
| 8 | **Past audit reports** | treated as normal supp docs (can become false risk) | own index (recurrence context) + summary; **never auto-confirm** | Medium |
| 9 | **SI History / Legal** | none | own index (recurrence context) + structured line-item display | Medium |
| 10 | **TPI Inventory source** | fixed volume file | read from uploaded supporting doc | Low |
| 11 | **Output / pages** | one Excel | AI Risk Analysis + Risk Summary + Scope pages (app) | **High (app)** |
| 12 | **"Evidence not requested ≠ risk"** | Gate C in place | same | ✅ Done |

\* Low **once the Likelihood definitions are provided** (matrix has Impact defined, Likelihood not).

---

## 5. WHAT IS REUSED (not rebuilt)

- The **per-question retrieval + policy/HCBI/supporting-doc analysis engine** — becomes Layer 1
  (raw findings), demoted from "deliverable" to "internal intermediate".
- **Vector index infrastructure** (`index_manager`, Direct-Access pattern) — extended with two new
  indexes (past audit, SI/Legal), same mechanism.
- **Gate C** logic (absence of evidence ≠ risk) — already matches step 7b.
- **Databricks connectivity / volumes / Delta tables** — same platform.

---

## 6. THE ONE HARD ITEM: TRANSACTIONAL DATA

**Why it's the crux (not the others):**
1. **Wrong form for aggregation** — in-doc data is chunked in the vector store; you cannot sum a
   column from chunks. (Whole-doc "fetch all chunks + 1 LLM call" works for a *summary*, but not for
   correct arithmetic over large tables.)
2. **Dynamic columns** (step 7d) — no fixed schema → no templated SQL → analysis must adapt per audit.
3. **Latency/cost** — if the LLM touches raw rows, it bursts.
4. **Two sources** — Databricks 7 dashboards **and** in-doc tables, both feeding the same risk pool
   early (step 7a).

**Bounded approach (keeps latency flat):**
- **Code** computes all aggregates / groupings / outlier flags (fast, deterministic, correct).
- **LLM** only interprets a **small summary** (a few hundred tokens) → flat latency regardless of
  table size.
- **Scope question for business:** how much risk-relevant transactional data is *only* in docs vs.
  already in the 7 dashboards? If dashboards cover it, the in-doc lane is minimal.

---

## 7. DELIVERY PHASING

| Item | Oct 5 | Phase 2 | Blocked on |
|---|---|---|---|
| Risk-centric aggregation (point 1) | ✅ | | Fields tab |
| Risk Matrix scoring | ✅ | | **Likelihood definitions** |
| Drop per-question scope tagging | ✅ | | — |
| Past audit — context/recurrence (index) | ✅ | | source content available |
| Past audit — summary (fetch-all-chunks + 1 LLM call) | ✅ | | Fields tab |
| SI/Legal — context/recurrence (index) | ✅ | | interim input path |
| SI/Legal — structured line-item display | ✅ | | Fields tab |
| Supporting-doc ↔ response linkage | ✅ | | app metadata (or interim map) |
| Transactional — Databricks, bounded (code→LLM summary) | 🟡 scope depth | | table/schema access |
| Transactional — in-doc tables | 🟠 minimal | full dynamic-table intelligence | business sizing |
| Multi-page application (upload UI, View Details, Generate Scope, checkboxes) | | 🔴 | app team |
| 7-dashboard "intelligent, no-fixed-formula" full depth | | 🔴 | scope + time |

---

## 8. BLOCKERS (need now)

1. **'Fields' tab** (points 1–5) — defines the exact output fields for Past Audit section,
   SI/Legal, AI Risk Analysis page, Risk Summary, Scope page. Cannot finalize output schema without it.
2. **Likelihood definitions** — the 5-level criteria for the matrix axis (Impact is defined; Likelihood
   is not). Cannot score category without it.
3. **7 dashboards → Databricks tables** mapping + schemas.
4. **Scope boundary** — are we building the **AI engine only** (data/API the app consumes) or the
   **app + engine**? Biggest effort driver.

---

## 9. RECOMMENDED COMMIT LINE (Oct 5)

Deliver the **AI engine / data layer**:
- risk-centric aggregation + **matrix** scoring,
- past-audit and SI/Legal **as context (indexes) + summary/line-item output**,
- supporting-doc↔response linkage,
- transactional **bounded** (Databricks dashboards; in-doc minimal),

output as the **sheet / API the application consumes** — with the **multi-page application** and the
**full dynamic-table transactional intelligence** as **Phase 2**.

> Point 1 (risk-centric) is a **pre-existing spec requirement**, not new scope — accept it.
> The legitimate scope/timeline conversation is about the **application** and the **depth of
> transactional intelligence**, plus the two blockers (Fields tab, Likelihood defs) that gate Oct 5.

---

## 10. KNOWLEDGE ARCHITECTURE (FCPA-EXPERT)

**Mental model:** one FCPA expert with perfect memory, working one engagement at a time. Each run is
scoped to an auditor (`AUDITOR_ID`), but the expert's memory (policies, past audits, SI/Legal, prior
transactional findings) is a shared, growing knowledge base, partitioned so the expert always knows
whose data is whose.

### Two layers
- **Knowledge layer (grows across all auditors; partitioned by `auditor_folder` + `scope_id`):**
  - Global policies / HCBI / scope-area guide — what the rules are (shared, no auditor tag).
  - Past-audit + SI-Legal — what was seen before (institutional memory, per-auditor tagged).
  - Transactional-analysis + raw-tables — what the current-year numbers say (per-auditor tagged).
- **Reasoning layer (per engagement — one auditor):** current-year-first (Phase 3 up front),
  evidence-over-assumption (Gate C/D/E/F), consistent scoring (Impact × Likelihood matrix),
  recurrence-aware retrieval, and dot-connecting aggregation.

### The scoping guarantee (strictly per-auditor)
> Every KB retrieval is filtered by `auditor_folder = current AUDITOR_ID`.

- Past-audit, SI-History & Legal, local policies, and transactional findings belong to a single
  auditor and are **never shared across auditors** — Sam_1's data applies only to Sam_1's run.
- Adding more auditors to the indexes never leaks into another auditor's run.
- Per-query cost stays flat regardless of how many auditors are stored (vector filter + top-k).
- The same code path serves 1 auditor or 50; the team keeps uploading per-auditor over time.

### One abstraction: `KnowledgeRetriever`
Every consumer goes through it, so scoping / caching / top-k are enforced in one place — no per-index
agents, no dumping.
```
KnowledgeRetriever(index_manager, auditor_id)
  .recurrence_for(query)              # past-audit + SI-Legal (per-auditor)
  .local_policy_for(query)           # local policies (per-auditor)
  .transactional_for(query, scope_area=...)
  # every call: auditor_folder filter, top-k, per-run cache
```

### Consumption pattern (accurate + scalable)
- **Per-question retrieval, gated to findings, cached, rolled up per risk.** Retrieval count scales
  with *findings* (dozens), not *questions × indexes* (thousands). Question-level precision at
  capture; risk-level consolidation at output (Past Occurrence Y/N + link).
- **Transactional-analysis** stays a bounded **injection** (small, cross-cutting pre-knowledge).
- **Raw-tables** feed NL2SQL awareness; the SQL agent also introspects live.

### Expert-trait → component
| Expert trait | Component |
|---|---|
| Knows the rules | policy / HCBI / scope indexes |
| Remembers prior findings | past-audit + SI-Legal (partitioned memory) |
| Reads the numbers first | Phase-3-first + transactional injection |
| Judges from evidence, not gaps | Gate C/D/E/F |
| Scores consistently | Impact × Likelihood matrix |
| Spots recurrence | gated per-finding retrieval, roll-up per risk |
| Isolates each client | `auditor_folder` filter (always) |

**Strictly per-auditor isolation** — past-audit and SI-History & Legal apply only to their own
auditor and are never cross-referenced across auditors. Built on the existing foundation
(`auditor_folder` + `scope_id` on every chunk, unified `index_manager`).
