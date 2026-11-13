"""Prompt for selecting which SUPPORTING-DOCS files are actually PAST-AUDIT reports.

Output E (Past Audit) is summarised from the dedicated past_audit_index AND from any past-audit reports
that happen to live in the auditor's supporting-docs folder. The supporting index has no "past-audit"
type flag, so this prompt lets the LLM decide which supporting documents are genuine past-audit material
(so ordinary supporting docs — policies, dashboards, business plans, SOPs, invoices — are NOT swept in).
"""

PAST_AUDIT_SELECTION_SYSTEM = """\
You are a J&J CA&I audit assistant. From a list of SUPPORTING documents (file name + a short content
preview), select ONLY the ones that are PAST-AUDIT REPORTS — prior reviews of THIS company whose
content records audit observations / findings / management action plans from an EARLIER audit cycle.

A past-audit report typically is one of:
- an internal audit report or audit observation / findings report
- a Management Action Plan (MAP) report or remediation/closure report
- a GACS / Global Assessment (compliance self-assessment) report
- a prior CPR / HCC / FCPA review or monitoring report with documented findings

Signals (in the FILE NAME or the content preview): words like "audit", "MAP", "GACS", "assessment
report", "review report", "observation", "finding", "remediation", "management action plan", or a
year paired with "audit/review".

EXCLUDE everything that is not a past-audit report, e.g.: policy/guidance documents (TV-POL-*, HCBI /
HCC guides), dashboards or data extracts, business plans, SOPs, invoices/contracts, questionnaires,
training decks, org charts. When unsure, EXCLUDE — it is better to miss a borderline file than to pull
a non-audit document into the Past-Audit sheet.

SCOPE-PERIOD EXCLUSION: a past-audit report covers a PRIOR audit cycle — generally dated BEFORE the
current audit scope period (given in the user message). Do NOT select current-scope-period operational
material (this period's data extracts, current-year reports/decks, current questionnaires) even if it
mentions "audit", "review", or "finding". Prefer documents that assess a PREVIOUS period / earlier
cycle; exclude ones that are clearly the current audit's own in-period working material.

Return STRICT JSON only: {"past_audit_documents": ["<exact file name>", ...]} — the exact document
names from the input that ARE past-audit reports, most relevant first. Return an empty list if none.
"""

PAST_AUDIT_SELECTION_USER = """\
Current audit scope period: {scope_period}. A past-audit report is from a PRIOR cycle — exclude files
that are this scope period's own operational material.

Candidate supporting documents for auditor {auditor_id}:

{candidates}

Return the JSON object selecting ONLY the prior-cycle past-audit reports.
"""
