# AI Auditor — Risk Scoring Algorithm Overview
**Project:** J&J TPI Due Diligence — Enterprise AI Audit Engine
**Prepared by:** EXL Analytics
**Date:** August 2026

---

## 1. Overview

The AI Auditor processes each questionnaire row through a multi-stage pipeline of six AI agents and one rule-based transactional analysis module. Every row produces a **Risk Score (1–10)**, a **Risk Type**, and a **Risk Rationale** grounded exclusively in the permitted policy documents and supporting evidence provided.

The pipeline runs in two phases:

| Phase | Scope | Trigger |
|---|---|---|
| Phase 1 & 2 — Questionnaire Analysis | One pipeline run per questionnaire row | Runs for every question |
| Phase 3 — Transactional Data Analysis | One run per audit scope | Runs once at end of audit |

---

## 2. Knowledge Base (Vector Search Indexes)

Before the pipeline begins, all source documents are chunked (800 tokens, 100-token overlap) and embedded using **Azure OpenAI text-embedding-3-small (1536 dimensions)** into three Databricks Vector Search indexes:

| Index | Source | Contents |
|---|---|---|
| HCBI Index | `/Volumes/.../global_policies` | Global HCBI Policy (TV-POL-02257, TV-POL-02312) |
| Policy Index | `/Volumes/.../local_policies_tpi` | TPI-specific SOPs, guidelines, work instructions (16 documents) |
| Supporting Docs Index | `/Volumes/.../supporting_documents` | All IDR evidence files (PDFs, Excel, Word, emails, etc.) |

At query time, each agent retrieves the top-k most semantically relevant chunks for the question being assessed.

---

## 3. Source Hierarchy

When policy sources conflict, the following priority order is applied:

```
1. HCBI Guide (Global Policy)           ← highest authority
2. TPI Due Diligence Policy
3. Official supporting documents
4. Questionnaire responses
5. Narrative statements                 ← lowest authority
```

The higher-priority source always governs. Conflicts are identified in the Risk Rationale; a conflict alone does not constitute non-compliance.

---

## 4. Phase 1 & 2 — Per-Question Pipeline

Each questionnaire row passes sequentially through six agents:

```
Question + Response
        │
        ▼
┌─────────────────────┐    ┌──────────────────────┐    ┌───────────────────────┐
│  HCBI Policy Agent  │    │ Policy Knowledge Agent│    │ Supporting Docs Agent │
│  (HCBI index RAG)   │    │  (Policy index RAG)   │    │ (Supporting index RAG)│
└──────────┬──────────┘    └──────────┬────────────┘    └────────────┬──────────┘
           │                          │                               │
           └──────────────────────────┴───────────────────────────────┘
                                      │
                                      ▼
                         ┌────────────────────────┐
                         │  Risk Evaluation Agent  │
                         │  (Gap Analysis + Level) │
                         └────────────┬────────────┘
                                      │
                   ┌──────────────────┴──────────────────┐
                   │ transactional_intent_detected = true? │
                   └──────────┬──────────────────────┬─────┘
                           YES│                    NO │
                              ▼                       │
                 ┌─────────────────────────┐          │
                 │ Transactional Data Agent│          │
                 │   (NL-to-SQL on Delta)  │          │
                 └────────────┬────────────┘          │
                              └──────────────┬─────────┘
                                             ▼
                                ┌────────────────────────┐
                                │   Risk Analyzer Agent   │
                                │  (Final Score + Output) │
                                └────────────────────────┘
```

### 4.1 HCBI Policy Agent
Performs semantic retrieval (top-5 chunks) from the HCBI global policy index. Provides the HCBI framework context to all downstream agents.

### 4.2 Policy Knowledge Agent
Performs semantic retrieval (top-5 chunks) from the TPI-specific policy index. Provides the applicable local policy context.

### 4.3 Supporting Docs Agent
Performs semantic retrieval (top-5 chunks) from the supporting documents index. Retrieves the most relevant evidence files submitted by the auditee for the specific question.

### 4.4 Risk Evaluation Agent
Receives the accumulated context from all three retrieval agents and performs:

1. **Compliance gap identification** — maps each policy requirement against the auditor response
2. **Risk level assignment** — assigns one of: `High | Medium | Low | No Risk`
3. **Transactional intent detection** — flags `true` if the question involves payments, revenue, credit memos, financial KPIs, or quantitative claims that can be verified against transactional data

Output is structured JSON containing compliance gaps, risk level, and transactional intent flag.

### 4.5 Transactional Data Agent *(conditional)*
Activated only when `transactional_intent_detected = true`. Uses LLM-generated SQL (NL-to-SQL) to query three Databricks Delta tables:

| Table | Content |
|---|---|
| `payments_dashboard_summary` | TPI payment transactions — vendor, invoice amount, GL account, date |
| `revenue_dashboard_credit_memo` | Credit memo and returns data |
| `revenue_dashboard_summary` | TPI revenue — customer, billing net value, transaction type |

Every SQL query is scope-filtered (`WHERE scope_id = '<scope>'`). The agent returns quantitative evidence that validates or contradicts the auditor response.

### 4.6 Risk Analyzer Agent
Performs the final 12-step sequential assessment:

| Step | Action |
|---|---|
| 1 | Identify the applicable policy requirement |
| 1A | Cross-check against the 12 Named Risk Scenario Catalog |
| 2 | Determine if the question is risk-based (vs. background/role information) |
| 3 | Classify the response: Meaningful / Generic / Blank |
| 4 | Evaluate "No" responses (not auto-flagged as risk) |
| 5 | Review supporting evidence and classify relationship to response |
| 6 | Identify the exact evidence gap (not vague language) |
| 7 | Test for non-compliance (all 3 conditions must be met) |
| 8 | Avoid unsupported assumptions |
| 9 | Assign Risk Type |
| 10 | Assign Risk Score |
| 11 | Prepare the 7 output columns |

---

## 5. Risk Classification Framework

### 5.1 Three-Tier Risk Type

| Risk Type | Conditions for Use |
|---|---|
| **Potential Risk** | (1) Specific policy requirement exists AND (2) Relevant response/evidence exists AND (3) Evidence clearly demonstrates deviation from requirement |
| **Further Review Required** | Applicable requirement exists + credible specific risk indicator exists, BUT evidence is insufficient to confirm or rule out non-compliance |
| **No Risk Identified** | Evidence consistent with requirement; OR no applicable requirement identified; OR question concerns only background/role/organizational information |

### 5.2 Risk Score (1–10 Scale)

| Score Band | Label | Risk Type | Conditions |
|---|---|---|---|
| **8–10** | High | Potential Risk | Mandatory policy requirement clearly violated; deviation supported by explicit evidence; significant control failure demonstrated |
| **5–7** | Medium | Potential Risk or Further Review Required | Policy deviation demonstrated (no significant control failure) OR credible material risk indicator requiring further review |
| **3–5** | Low | Further Review Required | Specific but limited unresolved issue; OR evidence partially supports response but policy-relevant element unverified |
| **1–2** | No Risk | No Risk Identified | No applicable requirement or no risk indicator |

**Consistency rule:** Risk Type and Risk Score must be consistent:
- Potential Risk → Score 5–10
- Further Review Required → Score 3–6
- No Risk Identified → Score 1–2

### 5.3 Evidence Classification (A–E)

| Class | Meaning |
|---|---|
| **A** | Directly cited in auditor response |
| **B** | Corroborating evidence |
| **C** | Tangentially related |
| **D** | Contradicting evidence |
| **E** | No relevant evidence found |

### 5.4 Evidence Relationship to Response

During Step 5, the agent internally classifies whether retrieved evidence:

| Classification | Meaning |
|---|---|
| Supports | Confirms all material elements of the response |
| Partially Supports | Confirms some elements; one or more policy-relevant elements cannot be verified |
| Contradicts | Directly conflicts with the response or demonstrates a policy deviation |
| Insufficient to Test | Evidence provided but cannot verify the response or control requirement |
| Not Applicable | Supporting documentation not expected for this question |

---

## 6. Named Risk Scenario Catalog (12 Scenarios)

The Risk Analyzer Agent cross-checks every response against 12 pre-defined TPI Due Diligence risk scenarios derived from the policy documents. These scenarios supplement (not replace) the general analysis.

| # | Scenario | Default Risk Level | Primary Policy Source |
|---|---|---|---|
| 1 | Government official relationships not identified during due diligence | High | HCBI Guide + TV-POL-02224 |
| 2 | Fair Market Value (FMV) assessment missing or unsupported | High | TV-GDL-02451 + HCBI Guide |
| 3 | Significant red flags not resolved before approval | High | TV-GDL-02453 + HCBI Guide |
| 4 | TPI incorrectly classified as out-of-scope or exempt | High | TV-POL-02224 + HCBI Guide |
| 5 | Required anti-corruption training / certifications not completed | Medium–High | HCBI Guide + Training Deck |
| 6 | Due diligence leverage used without meeting eligibility criteria | Medium–High | TV-GDL-02454 + TV-POL-02223 |
| 7 | Material changes not reassessed during lifecycle management | High | TV-POL-02223 + HCBI Guide |
| 8 | Sub-intermediary involvement not identified or assessed | High | TV-POL-02224 + TV-GDL-02453 |
| 9 | Business justification incomplete or unsupported | High | Business Justification Process |
| 10 | Reputation screening not completed or reviewed | Medium–High | TPI Due Diligence Process |
| 11 | Transaction occurred prior to due diligence completion | TBD* | Not yet confirmed |
| 12 | Periodic due diligence renewal not performed within cycle | TBD* | Not yet confirmed |

*Scenarios 11 and 12 are pending policy citation confirmation from the audit team. No Potential Risk or Further Review Required conclusion is drawn for these scenarios until confirmed.

---

## 7. Phase 3 — Transactional Data Analysis (Rule-Based)

Runs **once per audit** (not per question) at the end of the pipeline. Four deterministic SQL checks are executed against the Databricks Delta tables. The LLM is invoked only to generate the human-readable narrative for confirmed findings.

### Check 1 — Payment Concentration Mismatch
**Logic:** Queries the top 10 TPIs by total invoice amount from actual payment data. Compares against IDR questionnaire responses referencing maximum/highest/total payments. Flags if the actual top payer is not referenced in IDR responses.

| Output Field | Value |
|---|---|
| Risk Type | Further Review Required |
| Risk Score | **6.0** (Medium band) |
| Evidence Classification | Contradicts |

---

### Check 4 — Sales Revenue Concentration Risk
**Logic:** Calculates each TPI's share of total sales revenue. Flags any TPI whose share exceeds **50%** of total sales.

```sql
HAVING (SUM(Billing Item Net Value) / grand_total * 100) > 50
```

| Output Field | Value |
|---|---|
| Risk Type | Further Review Required |
| Risk Score | **4.0** (Low band) |
| Evidence Classification | Supports |

---

### Check 5 — Invalid GL Account Usage
**Logic:** Loads all approved GL account codes from the **HCC GL Account Map** reference file. Flags any TPI payment transaction using a GL code not present in the approved map.

```sql
WHERE GL_Account_Code NOT IN (<approved GL codes from HCC map>)
```

| Output Field | Value |
|---|---|
| Risk Type | Potential Risk |
| Risk Score | **6.0** (Medium band) |
| Evidence Classification | Supports |
| Reference File | HCC_GL_Account_Map-11142023.xlsx |

---

### Check 6 — Unknown Supplier (Not in TPI Inventory)
**Logic:** Loads all approved TPI entity names from the **TPI Inventory** file. Flags any supplier in payment transactions whose name does not match (substring match, case-insensitive) any approved TPI entity.

```sql
WHERE supplier_name NOT LIKE '%<any TPI inventory name>%'
```

| Output Field | Value |
|---|---|
| Risk Type | Potential Risk |
| Risk Score | **8.0** (High band) |
| Evidence Classification | Supports |
| Reference File | TPI_Inventory_Final_Output.xlsx |

---

## 8. Cross-Question Conflict Detection

After each question is processed, the system compares its response embedding against all previously processed responses in the same audit session using **cosine similarity**.

**Algorithm:**
```
similarity(A, B) = (A · B) / (||A|| × ||B||)
```

| Similarity Score | Severity |
|---|---|
| ≥ 0.97 | Critical |
| ≥ 0.93 | Moderate |
| Above threshold (< 0.93) | Minor |

Conflict flags are attached to the finding and included in the output. They indicate potential contradictory or duplicate information across questionnaire responses.

---

## 9. Output Columns (Per Finding)

Every finding (questionnaire-based and transactional) produces these columns in the output Excel:

| Column | Description |
|---|---|
| Risk Title | "Potential Risk" or "Further Review Required" |
| Risk Type | One-line description of the specific risk identified |
| Risk Score | Numeric 1–10 |
| Risk Score Reasoning | Explains the score band and the key evidence that determined it |
| Risk Rationale | Why this is a risk — policy/rule breached |
| Risk Description | Detailed explanation of what was found |
| Evidence Classification | A / B / C / D / E |
| Evidence Explanation | Explanation of the evidence relationship |
| Calculation Evidence | Row-level transactional values (transactional findings only) |
| Question | Original questionnaire question (questionnaire findings only) |
| Response | Original auditor response (questionnaire findings only) |
| Source Details | Document name, section, page references used |

**Note:** "No Risk Identified" rows are never written to the output file.

---

## 10. Technology Stack

| Component | Technology |
|---|---|
| LLM (reasoning + scoring) | Azure OpenAI GPT-5 (JNJ Gateway) |
| Embeddings | Azure OpenAI text-embedding-3-small (1536 dims) |
| Vector Search | Databricks Vector Search — Direct Access indexes |
| Agent Orchestration | LangGraph (multi-agent supervisor pattern) |
| Transactional Data | Databricks Delta Tables via SQLAlchemy |
| Document Extraction | PyMuPDF + pdfplumber (PDF), python-pptx (PPT), python-docx (Word), openpyxl (Excel), extract-msg (Outlook .msg) |
| OCR | Tesseract (for image-based PDFs) |
| Platform | Databricks Unity Catalog (catalog, schema, volumes, tables) |

---

*This document reflects the algorithm as implemented in the current version of the AI Auditor codebase.*
