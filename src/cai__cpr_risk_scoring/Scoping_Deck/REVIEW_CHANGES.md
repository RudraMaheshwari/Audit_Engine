# Scoping Deck — Review Change Log

Running record of files changed to address deck review points. Copy these files to the other PC
(`C:\Users\RMahesh8\Downloads\cai__cpr_risk_scoring\cai__cpr_risk_scoring\`) after each batch.

## Batch 1 — footer & opening/closing slide numbers (2026-09-28)

Review points addressed:
1. Footer logo not on the same line as the rest of the footer details → logo vertically centred on the
   footer text line and no longer distorted (aspect preserved).
2. Slide number not required on the opening and closing slides.
3. Opening slide (slide 1) was showing a slide number → removed.

Files changed:
- `src/deck/renderer.py` — shorthand footer logo aspect/vertical-centre fix; page number suppressed on
  the opening slide (page 1) and already absent on the closing slide.

## Batch 2 — Sam_1 dual-country slides, content corrections, Excel exporter (2026-09-29)

Review points addressed:

**Slide 2 (Market Overview)**
1. Sam_1 now generates TWO market overview slides — IM Australia and IM New Zealand — instead of one.
   Other auditors are unaffected (standard single slide).
2. Questionnaire ingestor now checks `/Volumes/nbod_gaagd/cpr_risk_analysis/questionnaire_data/Sam_1/`
   first for Sam_1; falls back to table-based repo if the path is absent or empty.
3. Market overview prompt corrected: Australia HCP % now 43% private / 38% public / 19% both
   (not "~50% public"); Medicare described correctly; tender points must be grouped together;
   NZ-specific facts (Pharmac, Medsafe, Medicines NZ Code) added.

**Slide 3 (Business Overview)**
1. Sam_1 now generates TWO business overview slides — IM Australia and IM New Zealand.
2. Currency rule added to prompt: always write AUD (not A$), NZD for New Zealand dollars.
3. No-hyphen rule added: "Fee for Service" not "fee-for-service".
4. Tender-related points must be consecutive (not scattered).
5. Key regulations prompt now requires: Therapeutic Goods Act 1989 (Volumes 1 and 2) and Medicines
   Australia Code of Conduct for AU; Medicines New Zealand Code of Practice and Medsafe for NZ.

**Slide 4 (Risks noted by Management)**
1. Fixed spec text: "risks areas" → "risk areas".
2. Prompt wording rules added: "Scientific, Non-Commercial & Commercial Independence" label;
   PSP bullet split (main point + "Interactions with TPIs with respect to PSP"); KOL payments bullet.

**Slide 5 (Legal Counsel)**
1. Sub-bullet support added to renderer: lines starting with "  - " (two-space indent) render as
   indented bullets (en-dash, deeper left margin). Prompt updated to instruct sub-bullet use for
   grouped PSP points.

**Slide 6 (SI Investigations)**
1. Wording rules added to SI prompt: "Increasing market share against company policy" (not
   "contrary to company policy"); no hyphen in "Fee for Service"; correct clinical trial / PI
   selection wording (Marketing included in cross-functional team; KOLs targeting PBAC); HCP
   expenses phrasing; vendor conflict-of-interest phrasing.

**Slide 7 (Past Review / CPR)**
1. Abbreviation rule changed: ALL abbreviations expanded to full forms (Fair Market Value,
   Government Official, Third Party Intermediary, etc.).
2. No-hyphen rule: "Proof of event" not "Proof-of-event", "Fee for Service" not "Fee-for-Service".

**Excel Reference Workbook**
1. New file `src/deck/excel_exporter.py` — generates a .xlsx alongside the .pptx after every run.
2. Columns: Slide | Section | Content | Source Type | Source Detail | Criticality |
   Content Justification.
3. Criticality column (Critical / Major / Minor) populated from CPR and GACS finding labels.
4. Content Justification column explains why each piece of content was included and what source
   type it came from.

## Batch 3 — CPI NZ, PSP sub-bullets, logo align, page numbering, sentence format, hyphens (2026-09-29)

Review points addressed:
1. CPI block now generated for New Zealand separately (iso3=NZL) and appended to NZ market overview slide.
2. Legal slide (slide 5): PSP points auto-grouped — first "(PSP)" bullet stays top-level; all
   subsequent "(PSP)" bullets in the same run become "  - " sub-bullets. Both the prompt example
   and a post-processing pass in app.py enforce this.
3. Opening slide logo nudged down: WORDMARK_TOP 6.95 → 7.02 to align with footer text baseline.
4. Page numbering starts at 1 on first content slide (market overview). Opening slide suppressed
   as before; all other slides now display page_no − 1.
5. Sentence formatting applied to all bullet content: first char capitalised, full stop appended
   if missing. Runs via _fmt() in app.py after generation, before render.
6. Hyphenated compound modifiers (non-compliant, pre-approved, pre-tender) kept in prompts;
   unhyphenated terms (Fee for Service, Proof of event, etc.) remain without hyphens.

## Batch 5 — colon consistency across all slides (2026-09-30)

Review points addressed:
1. All lead-term bullet formats now use a colon (":") not a dash ("–" or "—"):
   - risks_noted_prompt: bold-lead example changed from "**Term** – ..." to "**Term:** ..."
     with explicit rule "ALWAYS use colon after the bold lead term".
   - si_legal_prompts (Legal slide): bold-lead example already used "**Term:**"; rule added.
   - si_legal_prompts (SI slide): HCP expense wording rule changed from
     "sales reps – [flag details]" to "sales reps: [flag details]".

Files changed:
- `src/prompts/risks_noted_prompt.py` — bold-lead format uses colon; no-dash rule added.
- `src/prompts/si_legal_prompts.py` — bold-lead rule; HCP expense wording rule colon fix.

Also in this batch:
- `src/app.py` — double @staticmethod decorator on _fmt() removed (was causing caps-after-colon
  fix to fail in Python < 3.10); _fmt() now uses module-level re directly.
- `src/deck/renderer.py` — _safe() XML sanitizer added; all run.text assignments wrapped in
  _safe() to prevent "PowerPoint found a problem" corruption from LLM control characters.

---

## Batch 4 — logo alignment, footer overflow, caps after colon/dash, hyphen enforcement (2026-09-30)

Review points addressed:
1. Opening slide logo nudged further down: WORDMARK_TOP 7.02 → 7.10.
2. Market overview body height reduced 6.05 → 5.85 (prevents content overflowing into footer).
3. Sentence formatter (_fmt) now also capitalises the first letter after ': ' and ' – '/' — '.
   Examples: "Process design (PSP): unclear…" → "…: Unclear…";
   "sales rep – suspicious…" → "…– Suspicious…".
4. Hyphen rules expanded in all prompts: added pre-approval, sub-intermediaries,
   mis-posted alongside existing non-compliant / pre-approved / pre-tender.

Files changed:
- `src/deck/renderer.py` — WORDMARK_TOP 7.02 → 7.10.
- `src/deck/spec.py` — market overview body height 6.05 → 5.85 (all three slides).
- `src/app.py` — _fmt() gains _cap_after() inner function; capitalises after ': ' and ' – '.
- `src/prompts/si_legal_prompts.py` — hyphen rule expanded (pre-approval, sub-intermediaries,
  mis-posted added).
- `src/prompts/past_review_prompt.py` — same hyphen rule expansion.
- `src/prompts/risks_noted_prompt.py` — same hyphen rule expansion.
- `src/prompts/business_overview_prompt.py` — same hyphen rule expansion.

---

Files changed (Batch 3):
- `src/deck/renderer.py` — WORDMARK_TOP 6.95 → 7.02; page_no − 1 for displayed number.
- `src/app.py` — _build_cpi_block_for(iso3, country, year); future_cpi_nz for Sam_1; NZ CPI
  passed to NZ market overview; _group_psp_subbullets() post-processor; _fmt() sentence formatter
  applied to all bullet content before render.
- `src/prompts/si_legal_prompts.py` — explicit PSP sub-bullet example added; hyphen rule added.
- `src/prompts/past_review_prompt.py` — non-compliant/pre-approved/pre-tender hyphen rule added.

Files changed:
- `src/deck/spec.py` — fixed "risk areas"; added SAM_1_DECK_SPEC with AU+NZ market and business
  overview slides; added _business_table_keyed helper; added sub-bullet spec items.
- `src/deck/renderer.py` — added sub-bullet rendering (_has_sub_marker, _set_sub_bullet,
  _strip_sub_bullet) with deeper indent and en-dash character.
- `src/deck/excel_exporter.py` — NEW: reference Excel workbook exporter.
- `src/app.py` — Sam_1 dual-country generation; SAM_1_DECK_SPEC selection; volume path pass-through;
  _export_reference_excel wired in after render.
- `src/market_overview/generator.py` — added country parameter for per-country generation.
- `src/business_overview/generator.py` — added entity_override + key suffix for AU/NZ sections.
- `src/ingestion/questionnaire_ingestor.py` — added volume_path param; _load_chunks_from_volume
  reads Parquet/JSON/CSV from Volumes path, falls back to table repo if absent.
- `src/prompts/market_overview_prompt.py` — Australia/NZ specific fact rules; tender grouping;
  HCP % correction; no-hyphen rule.
- `src/prompts/business_overview_prompt.py` — AUD currency; fee for service; Therapeutic Goods Act
  1989; Medicines NZ Code; tender ordering rule.
- `src/prompts/risks_noted_prompt.py` — wording rules for Scientific/Non-Commercial/Commercial
  Independence; PSP sub-point; KOL payments; fee for service.
- `src/prompts/si_legal_prompts.py` — SI wording rules + sub-bullet instruction for Legal slide.
- `src/prompts/past_review_prompt.py` — full abbreviation expansion; no-hyphen rule.
