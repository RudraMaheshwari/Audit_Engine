# Skills & MCP — Positioning for the FCPA / TPI Due-Diligence AI Auditor

**Audience:** CA&I leadership, reviewers, and the engineering team
**Purpose:** Explain why *not* adopting "Skills" and "MCP" today has **no impact on output quality**, what we do instead on our LangGraph + GPT‑5 stack, and how both will be adopted later as **reusability / dynamic‑tooling** enhancements — not as accuracy levers.

---

## 1. TL;DR

- **Skills do not improve analytical quality.** They are a **reusability and packaging** mechanism — a standard way to bundle an instruction + its resources so a capability can be re‑used across agents/projects. Quality is driven by the **reasoning model, the prompts, the retrieval (RAG), and the validation logic** — none of which change because a capability is (or isn't) wrapped as a "Skill."
- **"Skills" are native to Google ADK and to Claude.** In those runtimes a skill is a first‑class primitive the framework orchestrates automatically, which is why skill‑packaged agents look clean and produce good output *at the skill level there*.
- **Skills are not native to LangChain / LangGraph** (our current stack). We therefore deliver the **same capabilities** through **prompts + typed tools + graph orchestration** — with identical output quality.
- **Planned path:** once our output is **validated and stable**, the same validated capabilities will be **consumed via tool‑calling** (and, if useful, packaged as skills) for reuse — an engineering convenience, applied *after* correctness is proven.
- **MCP (Model Context Protocol)** is for **dynamic tool calling** (discovering/binding tools at runtime). It adds a **cost factor** and **a little per‑tool latency**, so it is **deferred**; it will be added later where dynamic tooling clearly pays for itself.
- **GPT‑5 is a strong fit as the reasoning model** for this workload and delivers the analytical quality; the framework around it (LangGraph) is orchestration, not intelligence.

---

## 2. What a "Skill" actually is (and isn't)

A **Skill** is a reusable capsule: an instruction/spec plus any helper resources, exposed so an agent can invoke it by name. Think of it as **a packaging + reuse convention**, not a new form of intelligence.

| A Skill **is** | A Skill **is not** |
|---|---|
| A reusable, named capability (instruction + resources) | A source of higher accuracy or better reasoning |
| A way to share one capability across many agents/projects | A replacement for good prompts or good retrieval |
| A runtime‑orchestrated primitive **where the runtime supports it** (ADK, Claude) | Something that changes the underlying model's judgement |
| An engineering/maintainability win (DRY) | A pre‑requisite for correct output |

**Implication:** whether our "assess a questionnaire answer" logic lives inside a *Skill* or inside a *prompt + tool* on LangGraph, **the model reasons over the same inputs and produces the same quality**. Skills change *how code is organised and reused*, not *what the model concludes*.

---

## 3. Why quality is unaffected

Output quality in this system is a function of four things — none tied to "skills":

1. **Reasoning model** — GPT‑5 performs the compliance reasoning, risk classification, and writing.
2. **Prompt engineering** — the scope, gates (evidence‑not‑requested ≠ risk, same‑party recognition, etc.), and output contracts we author.
3. **Retrieval (RAG)** — the four vector indexes (HCBI, global scope‑area policies, CPR guide, supporting evidence) that ground every answer.
4. **Validation & guardrails** — schema/structure validation, conflict detection, and the classifier→writer separation.

Skills sit *above* all of this as a packaging layer. Removing that layer (as we have, on LangGraph) removes **no** quality input. This is why the "skilled" outputs seen on ADK/Claude are good **because of the model + prompts + retrieval underneath**, which we reproduce exactly.

---

## 4. Native support — where "Skills" come from

- **Google ADK** — Skills are **native** (first‑class). ADK's runtime discovers and orchestrates skills for you; the fieldwork GenAI team uses ADK + `SKILL.md`, so skill‑level output is clean there.
- **Claude** — **natively accepts Skills** (Agent Skills: packaged instructions + resources the model loads on demand). This is why Claude produces strong output "at the skill level."
- **LangChain / LangGraph (our stack)** — **Skills are not a native primitive.** LangGraph's primitives are **graph nodes, tools, and state**. So we express the same capabilities as **prompts + typed tools + a supervisor graph** — same result, different packaging.

> **Bottom line:** "good output at skill level" is a property of the **model + runtime**, not of the skill wrapper. On LangGraph we get the same by wiring the capability as a tool/node.

---

## 5. How we do it today instead of Skills (LangGraph + GPT‑5)

Our capability model, without a "skills" abstraction:

- **Supervisor graph** orchestrates 8 specialist agents (scope identifier, HCBI, policy‑knowledge, supporting‑docs, risk‑evaluation, transactional, classifier, writer).
- **Typed tools** perform retrieval and SQL (vector search per index, transactional SQL) — the reusable "capabilities."
- **Prompts** encode the audit logic and guardrails (the "instructions" a skill would otherwise carry).
- **GPT‑5** provides the reasoning; **validation** enforces structure and consistency.

This is **functionally equivalent** to a skills‑based design; the difference is purely organisational.

---

## 6. ADK vs LangGraph — how the same thing is achieved

| Aspect | Google ADK (skill‑native) | LangGraph — our stack |
|---|---|---|
| **Core primitive** | Agents + **Skills** (native `SKILL.md`) | Graph **nodes + tools + state** |
| **Reusable capability** | Packaged as a **Skill** | Packaged as a **typed tool / node** |
| **"Instructions" for a capability** | Inside the Skill spec | Inside the **prompt** for the node/agent |
| **Orchestration** | Runtime auto‑orchestrates skills | Explicit **supervisor graph** (deterministic control) |
| **Tool calling** | Native (incl. skills as tools) | Native tool‑calling on the LLM |
| **Reasoning model** | Model‑agnostic (often Gemini) | **GPT‑5** (strong reasoning for compliance judgement) |
| **State / memory** | Session/state services | Graph state + repositories |
| **Dynamic tooling** | Via ADK toolsets / MCP | Static/typed today; **MCP later** |
| **Quality driver** | Model + prompts + retrieval | **Same** — model + prompts + retrieval |
| **Net effect on output** | — | **Identical quality**; skills only change reuse/packaging |

**Reading of the table:** the only rows that touch *quality* are "reasoning model," "instructions (prompts)," and "retrieval" — and those are equivalent (indeed, GPT‑5 is a strong reasoning choice here). Every other difference is about **packaging and orchestration**, not correctness.

---

## 7. Why GPT‑5 is the right reasoning model here

- Strong multi‑step reasoning over long policy + evidence context — exactly what FCPA risk analysis needs.
- Reliable structured output (the classifier/writer field contracts).
- Handles the nuanced gates (evidence‑not‑requested ≠ risk, same‑party vs affiliate distinction) well.

The framework (LangGraph) is the *conductor*; **GPT‑5 is the intelligence**. Choosing LangGraph over a skill‑native runtime does not downgrade the model or its reasoning.

---

## 8. Roadmap — Skills, later, via tool‑calling

Once the pipeline's **output is validated and stable**, we will:

1. **Freeze the validated capabilities** (retrieval, scope assignment, risk classification, writing).
2. **Expose them as reusable tools/skills** so they can be **consumed via tool‑calling** across auditors, scope areas, and future engagements.
3. Adopt a skill‑style packaging **only as a reuse/maintainability step** — after correctness is proven, never as a way to "get better answers."

This sequencing is deliberate: **validate first, package for reuse second.**

---

## 9. MCP (Model Context Protocol) — deferred, with reasons

**What it is:** MCP standardises **dynamic tool calling** — an agent can discover and bind to external tools/servers at runtime rather than having them hard‑wired.

**Why we defer it (for now):**

| Factor | Consideration |
|---|---|
| **Purpose** | Dynamic tool discovery/binding — valuable when the tool set changes at runtime; our tool set is currently fixed and known. |
| **Cost** | Additional runtime/infrastructure (MCP servers, connections) → a real **cost factor** to justify. |
| **Latency** | Each MCP tool call adds **a little latency** (discovery/handshake + round‑trip) on top of the LLM call. |
| **Maturity fit** | Not needed while tools are static and validated; adds moving parts before correctness is locked. |

**Plan:** MCP will be **added later** for dynamic tool calling once (a) the tool surface benefits from runtime flexibility and (b) the cost/latency trade‑off is justified. Until then, our **static, typed tools** give the same functional coverage with lower cost and latency.

---

## 10. Conclusion

- **Skills and MCP are reusability / dynamic‑tooling conveniences — not quality levers.**
- On **ADK/Claude** they're native, which is why skill‑level output looks good there; the *quality* comes from the **model + prompts + retrieval**, which we fully reproduce on **LangGraph + GPT‑5**.
- We therefore lose **nothing** in output quality today, and we keep a clean path to **adopt skills (via tool‑calling) after validation** and **MCP (for dynamic tooling) when the cost/latency is justified.**
