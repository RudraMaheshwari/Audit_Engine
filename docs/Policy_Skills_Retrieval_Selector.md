# Policy Layer — Skills as the Knowledge Unit, Retrieval as the Selector

How the policy/SOP knowledge layer works when policies are packaged as **Skills** (SKILL.md
folders) instead of RAG chunks — while retrieval is retained purely to **select which skill to
load**. This keeps our current stack (LangChain · Azure OpenAI GPT-5 · Databricks) and gains the
governance/defensibility benefits of the Skills pattern.

---

## The core idea

- **Skill = the knowledge unit.** Each policy / SOP / CPR scope is a self-contained folder:
  `SKILL.md` (name · description · metadata · Purpose · When To Use) + a `references/` folder with
  the full policy content. The *whole* policy is the unit — not fragments.
- **Retrieval = the selector only.** We do **not** embed the full policy text. We embed only each
  skill's **descriptor** (name + description + metadata) into a small "skill catalog" index. At
  query time we semantically match the question/risk to the right skill(s), then **load those whole
  skills** into the agent's context (progressive disclosure).

So retrieval never returns policy *content* — it only answers *"which policy skill(s) apply here?"*.
The agent then reasons over the complete, versioned policy.

---

## Flow diagram

```mermaid
flowchart TB

    %% ─────────────── BUILD TIME (once / on policy update) ───────────────
    subgraph BUILD["🏗️  BUILD TIME  ·  author + index skills (once, or on policy update)"]
        direction TB
        POL[/"Policy sources\nHCBI Global Policy · TPI DD SOPs\nCPR scope guidance (48 areas)"/]
        AUTH["Author / convert → SKILL.md folders\nname · description · metadata\n(scope_area · document · region · version)\nPurpose · When To Use · references/"]
        CAT[("🧭 Skill Catalog Index\nembeds ONLY name + description + metadata\n(the descriptors — NOT the policy body)\nDatabricks Vector Search")]
        STORE[("📁 Skill Files Store\nSKILL.md (L2 body) + references/ (L3)\nDatabricks Volume / repo")]

        POL --> AUTH
        AUTH -->|"descriptor (L1)"| CAT
        AUTH -->|"full skill content (L2 + L3)"| STORE
    end

    %% ─────────────── RUN TIME (per question / per identified risk) ──────
    subgraph RUN["⚡  RUN TIME  ·  per question / per identified risk"]
        direction TB
        Q[/"Question + Response\n(or identified Risk)"/]
        EMB["Embed query\ntext-embedding-3-large (3072-dim)"]
        SEL{{"🔎 RETRIEVER = SELECTOR\nsemantic search over skill descriptors\n→ top-k matching skills\n(+ optional metadata filter / rerank)"}}
        LOAD["📥 Load selected skill(s)\nprogressive disclosure:\nL2 = SKILL.md body\nL3 = references/ on demand"]
        INJ["Inject WHOLE-policy content\ninto the agent prompt"]
        AGENT["🤖 Policy / Risk agent reasons\nover the full policy skill(s)\n(GPT-5)"]
        OUT[/"Finding + provenance\nwhich skill · policy · version was applied"/]

        Q --> EMB --> SEL
        SEL -->|"selected skill ids"| LOAD
        LOAD --> INJ --> AGENT --> OUT
    end

    %% ─────────────── CROSS LINKS (build feeds run) ─────────────────────
    CAT -.->|"descriptors searched"| SEL
    STORE -.->|"full SKILL.md + references loaded"| LOAD

    classDef build   fill:#1a3a2a,stroke:#2ecc71,color:#fff
    classDef store   fill:#0d3b6e,stroke:#3498db,color:#fff
    classDef run     fill:#2d1942,stroke:#9b59b6,color:#fff
    classDef sel     fill:#4a3500,stroke:#e67e22,color:#fff
    classDef io      fill:#1e3a5f,stroke:#4a90d9,color:#fff

    class POL,AUTH build
    class CAT,STORE store
    class EMB,LOAD,INJ,AGENT run
    class SEL sel
    class Q,OUT io
```

---

## Step-by-step

**Build time (once, or whenever a policy changes)**
1. **Author skills** — each policy / SOP / CPR scope becomes a `SKILL.md` folder: frontmatter
   (`name`, `description`, `metadata`: scope_area · document · region · version), a **Purpose** and
   **When To Use** section, and the full policy text under `references/`.
2. **Index descriptors only** — embed each skill's **name + description + metadata** into the
   **Skill Catalog Index** (Databricks Vector Search). The policy *body* is **not** embedded.
3. **Store skill files** — the `SKILL.md` bodies and `references/` live in a Databricks Volume /
   repo, loaded on demand.

**Run time (per question, or per identified risk)**
4. **Embed the query** — the question + response (or an identified risk) is embedded.
5. **Select (retrieval)** — semantic search over the **descriptor** catalog returns the top-k
   matching skills; optional metadata filter (e.g. region) or rerank refines it. This is the *only*
   place retrieval is used, and it returns **skill IDs, not content**.
6. **Load skill(s)** — the selected skills' full `SKILL.md` body (L2) and, on demand, their
   `references/` (L3) are loaded — progressive disclosure keeps context lean.
7. **Inject + reason** — the whole-policy content is injected into the agent prompt; the agent
   performs its analysis against the complete, versioned policy.
8. **Output with provenance** — the finding records **which skill / policy / version** was applied
   (clean audit trail), and this provenance can be written to the OTEL observability store.

---

## How this maps to our current stack (no framework migration)

| Concern | Implementation on LangChain + Azure GPT-5 + Databricks |
|---|---|
| Skill authoring | `SKILL.md` folders per scope/policy in a Databricks Volume / repo |
| Skill Catalog Index | Databricks Vector Search over **descriptors** (name + description + metadata) |
| Selector | our existing semantic retriever, pointed at the catalog (top-k + metadata filter) |
| Skill loader | small custom step: read selected `SKILL.md` + `references/`, inject into prompt |
| Progressive disclosure | load L2 body always; load L3 `references/` only if the agent needs more |
| Provenance | log skill name + version + model id to the OTEL / Feedback (Lakebase Postgres) stores |

> Native Skills loaders exist in the Claude Agent SDK and Google ADK; on our stack the loader
> (steps 5–6) is a thin custom component. The pattern is identical — only the "who loads it" differs.

---

## What changes vs today's RAG

| | Today (RAG) | Skills + retrieval-as-selector |
|---|---|---|
| What is embedded | Policy **chunks** (fragments) | Skill **descriptors** only |
| What retrieval returns | Text fragments | **Skill IDs** → then load whole policy |
| Unit the agent sees | Assorted chunks | The **complete, versioned** policy |
| Fidelity | Can miss clauses across chunks | Full policy — no missing clauses |
| Provenance | "chunks retrieved" | "policy X vN applied in full" |
| Editing | Re-chunk + re-embed | Edit one `SKILL.md` folder (SME-friendly) |
| Scale | Excellent for huge corpora | Best for a bounded, named set (our 48 fit) |

---

## Design notes (important for Risk Analysis)

- **Selection ≠ scope-first.** Match skills by **risk/topic semantics**, not by a pre-assigned
  scope area. This keeps the "assign scope *after* risk analysis" decision intact — the selector
  can return **cross-scope** skills, and a risk type may draw on several.
- **Retrieval does not disappear** — it is the selector. "Skills not RAG" here means *package whole
  policies as skills; use retrieval to choose which to load*, not *remove retrieval*.
- **Progressive disclosure controls cost** — load only the selected skills' bodies; pull `references/`
  only when the agent needs deeper detail.
