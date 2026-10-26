# Full Scoping Deck vs System Output — Risk Gap Analysis
**Engagement:** JNJ TPI Due Diligence | IM ANZ | **Date:** 2026-09-29

---

## How to Read This Document

Each section covers one area from the scoping deck. For each area:
- **Scoping Deck Risk Reasons** = exact reasons the team wrote in the deck for flagging that area
- **System Verdict** = what the AI auditor concluded (Potential Risk / FRR / No Risk)
- **Overlap / Gap** = whether system and scoping deck agree
- **Question Mappings** = every question the system mapped to this area, with the actual answer and why the system gave No Risk
- **Transactional Finding** = what the data analysis found
- **Justification** = why the gap or overlap exists

---

## 1. Educational Grants
**Scoping Deck Status:** IN SCOPE | **Team:** Naeem | **Scope:** Full

### Scoping Deck Risk Reasons
- GO flag is not consistently tracked under current procedures → risk that HCCO approvals are not being obtained when required
- Critical findings from prior CPR
- Average value of educational grants is high with individual outliers in the data

### System Verdicts

> **Note on Row 443 (IIS):** `RESEARCH_GRANTS__INVESTIGATOR_INITIATED_STUDIES` is **not mentioned in the scoping deck**. The scoping deck only covers Educational Grants. Row 443 is a system-generated finding outside the declared scope — it is shown here for completeness but should not be presented as an Educational Grants gap. It belongs under a separate "System-Only Findings" category.

| Source | Row | Verdict | What Was Found | Why This Verdict |
|---|---|---|---|---|
| Transactional | 434 | **Potential Risk** ✅ | 4 grants with approval date after event start date — Leukaemia Foundation (×2), ANZ Pulmonary Hypertension Society (×2). Also: repeat grants to same HCOs across multiple BUs (Haematology, Gastroenterology & Hepatology depts); round-figure flags present | **Why Potential Risk:** The breach is concrete and data-confirmed. Both the approval date and event start date fields are populated in the HCC dataset, so the before/after ordering is directly verifiable — not inferred. Named recipients, specific counts, and the control failure (retrospective approval undermines legitimate needs assessment and FMV validation) are all evidenced. Risk Category assigned as Medium Risk: recipients are associations, not confirmed public institutions, which kept it below High Risk but the breach itself was unambiguous |
| Transactional | 443 | **FRR** ⚠️ *(Not in scoping deck — IIS only)* | Multiple IIS/grant-category projects with paid amounts exceeding approved amounts and approval-sequence flags. Recipients include University of Melbourne, Westmead Hospital, Monash University, Canberra Health Services | **Why FRR not Potential Risk:** Three reasons. (1) A large portion of the dataset rows are missing `ACTIVITY_ID` and approved-amount fields — so the "paid > approved" calculation cannot be reliably run across the full population; (2) small variances observed may be explained by FX rates, tax, or milestone timing rather than a control breach — contract and invoice support is needed to rule these out; (3) no single IIS grant over USD 50,000 to a public institution is confirmed in the project-level data. The system found the pattern but lacked the supporting data to confirm it is a genuine overpayment rather than a data gap or currency artifact |

### Overlap / Gap Status
**✅ OVERLAP on grants pre-approval (Row 434)** — System confirmed the scoping deck's exact concern. "HCCO approvals not obtained when required" and "critical prior CPR findings" are directly evidenced by 4 post-event approvals to named organisations.

**ℹ️ Row 443 is not a gap against the scoping deck** — IIS was not in scope. It is an additional system finding. If IIS was intended to be part of the Educational Grants review, the scoping deck needs to be updated.

### Question Mappings — All Returned No Risk

The table below shows every interview question the system mapped to Educational Grants, why the system gave No Risk, and what type of question it was. Questions fall into four types — Background (BG), General Perception (GP), Awareness/Open-ended (AW), Off-topic (OT) — none of which test a specific grants control.

| Q# | Question | Answer | Question Type | Why System Said No Risk | What a Proper Grants Question Would Have Asked |
|---|---|---|---|---|---|
| Q27 | What HCC activities do you consider the biggest risk? | "Promoting products off-label" | GP | Response names a general risk theme with no scope-period control deviation evidenced | *"Can you walk us through how educational grant pre-approvals are obtained and documented?"* |
| Q47 | Biggest HCC risks / any FCPA concerns? | "PSP because of the nature of the activity" | GP | General risk views — no specific grants control assessed | *"Are grant approval dates ever finalised after the supported event has already begun?"* |
| Q51 | Background and how long in current position? | "11+ years in Healthcare Compliance at J&J, HCC Officer for IM Australia" | BG | Background/tenure — no compliance control assessed | N/A — background question should not be mapped to grants scope |
| Q55 | Describe your compliance program model / HCC team size? | "Only fully dedicated HCC head for IM ANZ, leverages MedTech support" | BG | HCC staffing description — no grants approval or FMV control tested | *"Who is responsible for reviewing and approving educational grant applications before the event?"* |
| Q60 | What % of time dedicated to compliance issues? | "20%" | BG | Time allocation — cannot surface grants approval timing gaps | N/A — irrelevant to grants controls |
| Q69 | Biggest HCC risks / country risks? | "PSPs and activities crossing commercial/non-commercial functions, particularly new product launches" | GP | General risk perception — no scope-period deviation evidenced | *"How are GO flags tracked on grant recipients to confirm HCCO approval thresholds?"* |
| Q92 | Aware of any enforcement actions — HCC or corruption? | "No." | AW | Awareness-only question — no grants control tested; self-reported denial | *"Have any educational grants been approved retrospectively — i.e., after the event has started?"* |
| Q96 | Corruption concerns regarding distributors? | "No concerns — professional relationship" | OT | Distributor question tagged to grants scope — completely off-topic | N/A — off-topic |
| Q111 | Background / how long in current position? | "Australian solicitor at J&J ANZ since 2017" | BG | Background only — no control | N/A — irrelevant |
| Q112 | Who do you report to / any dotted-line reporting? | "Emma Press, VP Law APAC Innovative Medicine" | BG | Reporting-line background — no grants control | N/A — irrelevant |
| Q125 | Is there a public mechanism to verify HCP affiliations? | "AHPRA in AU; NZ Medical Council has similar requirements" | AW | Informational — no grants approval or FMV control tested | *"How does the Company verify whether a grant recipient is a government-owned or public institution before approval?"* |
| Q127 | Any FCPA concerns — payments, vendors, HCPs? | "None that I am aware of." | AW | General attestation — self-reported; system flagged conflict with Qplanning_13 (similarity 0.920), meaning this denial closely matches other standardised denial responses | Self-reported "none" from a respondent who has not been asked about specific grants control failures |
| Q131 | Overview of background and compliance organisation regionally | "14 yrs J&J, APACIM Lead since Sept 2024" | BG | Background only | N/A — irrelevant |
| Q137 | Any FCPA risks/concerns — distributors, red flags, training, documentation? | "Not aware of any major risks" | AW | General catch-all — no specific control evaluated | *"Are all educational grant payments in the HCC system linked to an approved grant number and pre-event approval date?"* |
| Q138 | Any brands / BUs with greater risk for the affiliate? | "No" | GP | General brand-level perception — no grants control | Note: contradicts Q47/Q69 responses from other respondents who named PSP and cross-function activities |
| Q165 | Which BUs/product lines have biggest risks and why? | "Key products experiencing loss of exclusivity (e.g. Stelara)" | GP | Commercial/LOE risk — no FCPA control implicated | Off-topic for grants |
| Q170 | Anything we haven't discussed — corruption concerns? | "No." | AW | General awareness; no grants-specific deviation raised | Open-ended; the respondent has no prompt to think about grants timing or approval gaps |
| Q187 | Price ceilings for distributors per local law? | "Not responsible — refer to Mark Howard" | OT | Price ceilings — entirely off-topic for educational grants | N/A — off-topic |
| Q201 | Any interactions on healthcare system changes/implementations? | "No" | OT | No interactions reported — off-topic for grants | N/A — off-topic |
| Q231 | Background / how long in position? | "Economist, background in health economics, in role since April 2018" | BG | Background only | N/A — irrelevant |
| Q235 | Explain role of Access Planning / Health Economics Manager | "HE Managers lead PBS reimbursement dossier projects" | BG | Role description — no scope-period control or payment assessed | N/A — role context only |
| Q284 | Challenges maintaining compliance when working with distributors? | "N/A" | OT | Distributor framing — off-topic for grants | N/A — off-topic |
| Q372 | % HCPs/hospitals public vs private in your country? | "~50/50 Public/Private hospitals; ~50% HCPs in public sector" | AW | Background context — no grants control tested | Note: 50% public-sector HCP exposure is relevant context for GO flag tracking but was not connected to it |

### Justification
Of 23 questions mapped to Educational Grants, **0 tested a specific grants control**. The breakdown: 7 background/biographical, 6 general perception, 7 awareness/open-ended catch-alls, 3 off-topic (distributor or pricing questions incorrectly tagged). None asked about pre-approval timing, FMV validation, grant recipient GO status, documentation completeness, or HCC system data integrity. The transactional layer (Row 434) correctly found the control failure. The questionnaire No Risk verdicts reflect question design gaps, not compliance clearance.

---

## 2. Fee for Service (Doctor Consulting)
**Scoping Deck Status:** IN SCOPE | **Team:** Naeem | **Scope:** Full

### Scoping Deck Risk Reasons
- GO flag is not consistently tracked → risk HCCO approvals not obtained when required
- Critical findings from prior CPR (including repeat)
- GACS assessment includes critical observations
- Noted as a high-risk area by Finance Director — one of the largest expense areas
- SI activity
- HCC Committee Q1 2024 monitoring: **inadequate documentation maintained and duplicate payments confirmed**

### System Verdicts
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Questionnaire | 130 | No Risk | Open-ended catch-all — no concerns raised |
| Transactional | 430 | **No Risk** ❌ | Zero FFS rows returned — dataset was empty |

### Overlap / Gap Status
**❌ COMPLETE GAP** — No risk found at any level. Root cause is a data feed failure: `ENGAGEMENT_NUMBER IN (1000, 1001)` returned zero rows from the HCC user-input table. The system literally had no FFS transactions to test. This is not a clean result — it is an untested area.

### Question Mappings — All Returned No Risk

| Q# | Question | Answer | System Reason for No Risk | Gap Explanation |
|---|---|---|---|---|
| Q60 | What % of time dedicated to compliance? | "20%" | Time allocation background | Cannot surface FFS approval or documentation gaps |
| Q92 | Aware of any enforcement actions? | "No." | General awareness | Self-reported; HCC Committee minutes confirm Q1 2024 issues — respondent awareness not equivalent to absence of risk |
| Q130 | Anything we should be aware of — corruption concerns, media coverage on HCPs/consultants? | "None that I am aware of." | Open-ended; no concerns raised | Interviewee unlikely to self-disclose repeat CPR findings in an open-ended question |
| Q137 | Any FCPA risks — distributors, red flags, documentation, monitoring? | "Not aware of any major risks" | General inquiry; no control tested | "Not aware of" ≠ no risk; this is the exact area the FD flagged as high-risk |

### Transactional Evidence — Why Zero Rows
The evidence field explicitly states: *"Scope calculations restricted to ENGAGEMENT_NUMBER IN (1000, 1001) returned zero FFS rows from the HCC user-input table, so no FFS spend, GO classification, repeat HCPs, or GL coding exceptions were present to evaluate."*

This means none of the CPR red flags (missing FMV support, late approvals, repeat/public HCP engagements, GL misclassification, duplicate payments) could be tested because the HCC event extract did not include any FFS records for this engagement.

### Justification
The scoping deck's FFS risk basis comes from: (1) HCC Committee minutes confirming Q1 2024 duplicate payments and inadequate documentation; (2) Finance Director flagging as high-risk; (3) GACS critical observations; (4) prior CPR repeat finding. None of these are interview-discoverable. The questionnaire questions did not ask about FFS-specific controls (consulting contract documentation, FMV validation, repeat HCP usage, GO identification). The transactional layer returned empty — the risk is real and unassessed, not absent.

---

## 3. Congresses & JJ Organized Events
**Scoping Deck Status:** IN SCOPE | **Team:** Mandy | **Scope:** Full

### Scoping Deck Risk Reasons
- GO flag not consistently tracked → HCCO approvals not obtained when required
- Critical finding from prior CPR
- Multiple exception requests — HCPs asked to agree via CVENT instead of contract/invitation letter (MAP opened, revised procedure initiated)
- Monitoring identified **two forms of post-event documentation not retained**
- **5 transactions miscoded to GL HCC/HCBI – Advisory Board; confirmed with HCCO**

### Scoping Deck Transaction Volume
- AU: 215 transactions | AUD 1,452,514 / USD 958,296
- Canada: 5 transactions | NZD 9,244 / USD 5,593

### System Verdicts
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Questionnaire | 182 | No Risk | General project list — no FCPA control implicated |
| Transactional | 429 | **No Risk** ❌ | Zero congress/event transactions in dataset |

### Overlap / Gap Status
**❌ COMPLETE GAP** — Same data feed failure as FFS. `ENGAGEMENT_NUMBER IN (1000, 1001)` returned zero rows from the HCC dataset for congress/events. The scoping deck confirms AUD 1.45M in AU congress spend — this cannot be a zero-activity area.

### Question Mappings

| Q# | Question | Answer | System Reason for No Risk | Gap Explanation |
|---|---|---|---|---|
| Q182 | List all project types your group worked on in last 12 months | "All projects related to registration, maintenance, and reimbursement of products in AU/NZ and pricing" | General project types; no FCPA control | Regulatory/pricing focus — congress events not mentioned; framing excluded HCC-controlled event activity |

### Justification
The scoping deck confirms specific, documented issues: 5 GL-miscoded transactions (confirmed by HCCO), two forms of missing post-event documentation, a MAP already opened for CVENT exception requests, and a prior CPR critical finding. None of these can surface through a single general project-list question. The transactional result of zero rows is a data extraction problem — the congress HCC event records were not captured in the audit population.

---

## 4. Sponsorships
**Scoping Deck Status:** IN SCOPE | **Team:** Soojin | **Scope:** Full

### Scoping Deck Risk Reasons
- Critical finding from prior CPR (**repeats**)
- Recent GACS assessment
- Noted as a risk area by Finance Director
- GO flag not consistently tracked → HCCO approvals not obtained when required

### System Verdicts
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Transactional | 431 | **FRR** ⚠️ | Sponsorship documentation gaps + high spend concentration — Australasian College of Dermatology 18.09%, The Conference Company 11.57%, Urological Society of Australia 6.16%; several recipients are public institutions |

### Overlap / Gap Status
**⚠️ PARTIAL — System found transactional signals but stopped at FRR.** The scoping deck's risk (repeat CPR, GACS, FD-flagged, GO tracking gap) is directly supported by the system's finding of concentrated spend to public associations with documentation gaps. This should be Potential Risk given prior CPR history.

### Question Mappings
No questionnaire questions were mapped specifically to Sponsorships scope area. The FRR finding is entirely from transactional analysis.

### Transactional Evidence Detail
- Australasian College of Dermatology: USD 139,701.76 (18.09% of total)
- The Conference Company: USD 89,315.65 (11.57%)
- The Urological Society of Australia: USD 63,450 (6.16% approx.)
- Several recipients are public health associations — GO interaction documentation is required but not confirmed
- Documentation gaps: sponsorship approval completeness could not be confirmed from the dataset

### Justification
The FRR classification means the system found the pattern but cannot auto-confirm whether HCCO approvals and GO documentation are in place. Given the scoping deck states this is a **repeat CPR finding** with recent GACS observations and FD flagging, the threshold to escalate to Potential Risk is lower than for a first-time area. The concentration of spend in three recipients (combined ~36% of total) and their public-sector nature makes this a priority for manual confirmation.

---

## 5. Tenders
**Scoping Deck Status:** IN SCOPE | **Team:** Kaitlyn | **Scope:** Full

### Scoping Deck Risk Reasons
- Critical findings from prior CPR
- ~80% Public / 20% Private tender split — high government exposure
- Nominated wholesalers only allowed to sell J&J products at agreed price to the specific hospital that won the tender
- Pricing communicated to wholesalers; S100 product distribution fees claimed back through J&J
- All NZ prices negotiated through PHARMAC

### Scoping Deck Transaction Volume
- US (AU): 10 tenders won | AUD 22,326,064 / USD 14,729,612

### System Verdicts
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Questionnaire | Q396 | No Risk | No tender participation fees reported — aligns with HCBI |
| Transactional | 432 | **FRR** ⚠️ | Large credit memos to public hospitals post-award (Austin Hospital, Royal Hobart Hospital, Peter MacCallum Cancer Centre, Waikato DHB) with zero corresponding sales discount pattern |

### Overlap / Gap Status
**⚠️ PARTIAL — FRR found but not Potential Risk.** Credit memos to public hospitals post-tender with no discount rationale is the exact risk profile the scoping deck anticipates (post-award concessions to government customers). System found the signal but could not auto-confirm it represents an improper concession.

### Question Mappings

| Q# | Question | Answer | System Reason for No Risk | Gap Explanation |
|---|---|---|---|---|
| Q396 | Are fees required to participate in a public tender? | "There are no fees required to participate in a public tender." | Aligns with HCBI — no improper payments to participate | Question only tested one narrow risk (participation fees). Scoping deck risks (pricing controls, wholesaler restrictions, PHARMAC compliance, post-award concessions) were not questioned |

### Justification
The one questionnaire question mapped to Tenders tested only whether tender participation fees are paid — a narrow and unlikely risk. The scoping deck's actual risks (post-award pricing integrity, wholesaler restriction compliance, PHARMAC adherence, public hospital credit memo patterns) require transactional testing. The system found the credit memo pattern (FRR) which directly aligns with post-award concession risk. Given 80% public tender exposure and prior CPR findings, this FRR warrants escalation to Potential Risk.

---

## 6. Patient Support Programs
**Scoping Deck Status:** IN SCOPE | **Team:** Soojin | **Scope:** Full

### Scoping Deck Risk Reasons
- Recommended area by Legal
- Highlighted as a risk area by MD
- SI activity
- GACS assessment identified critical issues
- **TPI manage all aspects of support programs and directly interact with HCPs and patients on IM US's behalf**

### System Verdicts
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Transactional | 433 | **FRR** ⚠️ | USD 190,074 payment to Monash University (public-sector institution); AESIR HEALTH PTY LTD fee spike at 3.35× average in January; some PSP administrators not in FIRST TPI inventory |

### Overlap / Gap Status
**⚠️ PARTIAL — FRR found but not Potential Risk.** The system identified a payment to a government-linked HCO and unregistered PSP administrators — both directly matching the scoping deck's concern about TPIs interacting with HCPs/patients without proper oversight. Should be Potential Risk.

### Question Mappings

| Q# | Question | Answer | System Reason for No Risk | Gap Explanation |
|---|---|---|---|---|
| Q47 | Biggest HCC risks / any FCPA concerns? | **"PSP because of the nature of the activity — however through proactive monitoring and action plans the team has implemented relevant measures"** | Categorised as general risk views; no specific control assessed | **Critical miss:** Respondent explicitly named PSP as the biggest HCC risk. System did not cross-reference this with Row 433 (FRR). These two signals together should have elevated to Potential Risk |
| Q138 | Any brands / BUs with greater risk for the affiliate? | "No" | General perception question | Contradicts Q47 from a different respondent in the same interview file |
| Q170 | Anything we haven't discussed — corruption concerns? | "No." | General awareness; no deviation | Open-ended; respondent didn't repeat PSP concern here |
| Q228 | Challenges maintaining compliance when working with distributors? | "N/A" | General perspective; no control content | PSP administrators are not distributors — question framing excluded PSP-specific compliance challenges |

### Justification
Two independent signals point to PSP risk: (1) a respondent at Q47 explicitly named PSP as the biggest HCC risk; (2) the transactional layer found a USD 190,074 payment to a public university and an unregistered PSP administrator. The system treated these independently — Q47 as "general perception" and Row 433 as FRR — and never cross-referenced them. Together they constitute a Potential Risk. Additionally, unregistered PSP administrators (not in FIRST TPI inventory) directly interacting with public HCPs/patients is an FCPA exposure the scoping deck and Legal explicitly flagged.

---

## 7. Third-Party Payments & Due Diligence
**Scoping Deck Status:** IN SCOPE | **Team:** Kaitlyn | **Scope:** Full

### Scoping Deck Risk Reasons
- **Critical finding from prior CPR and major repeat finding**
- 81% of total sales come from wholesalers
- Multiple market research transactions miscoded to wrong GL; HCCO confirmed 2 payments miscoded
- In-scope review to include: wholesaler "true wholesaler" determination, payment classification, correct GL booking, due diligence completion, MAP review
- Lobbyists excluded (do not interact with government on behalf of J&J IM US)

### System Verdicts
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Transactional | 428 | **FRR** ⚠️ | Potential undisclosed TPIs — SOUTHERN CROSS MEDICAL CARE SOCIETY, CROHN'S COLITIS CURE, CROHN'S & COLITIS AUSTRALIA, CROHN'S & COLITIS, MICROWAY — payments made but none in FIRST TPI inventory |
| Transactional | 466 | **FRR** ⚠️ | 10,405 HCP/HCO disbursements (USD 562.9M) routed via one-time vendor (no VENDOR_ID); 661 distinct supplier names reused through this channel — bypasses FIRST DD controls |

### Overlap / Gap Status
**⚠️ PARTIAL — Two FRR signals but neither escalated to Potential Risk.** Both directly match the scoping deck's repeat DD finding. Row 428 confirms undisclosed TPIs (the exact prior CPR repeat finding). Row 466 at USD 562.9M is the most material finding in the entire dataset and represents systematic bypass of vendor master and FIRST due diligence controls.

### Question Mappings — All Returned No Risk

| Q# | Question | Answer | System Reason for No Risk | Gap Explanation |
|---|---|---|---|---|
| Q5 | What is your compliance role? | "Collaborate with HCC and Senior Leaders; ensure culture of accountability" | Leadership background; no FCPA control | Background description — cannot surface DD gaps |
| Q30 | Any FCPA concerns — payments, vendors? | "No" | General disclosure; none reported | Conflict with Qplanning_13 (similarity 1.000) flagged; self-reported denial |
| Q34 | Sales up/down? Did we meet business plan? | "NTS 2024 −1.2% vs BP24; Darzalex, Erlyand, Tremfya positive" | Sales performance; no FCPA control | Off-topic for DD |
| Q66 | Do you spend time on distributor compliance? | "No." | Background time allocation | Answer "No" to distributor compliance time is itself a concern given 81% wholesaler sales concentration |
| Q77 | Role in resolving DD red flags? | "HCCO reviewed red flags, works with LWG/RWG to resolve/mitigate" | Process aligns with TPI escalation requirements | Surface-level process described correctly — does not confirm whether it is executed in practice |
| Q110 | Any FCPA concerns — payments, vendors? | "No." | General attestation; none raised | Conflict with Qplanning_13 (similarity 0.975); another self-reported denial |
| Q117 | Board tone/commitment on HCC/FCPA? | "Board is aware and committed to HCC/FCPA compliance" | Perception question; no control | Tone question cannot surface TPI registry gaps |
| Q162 | Any FCPA concerns — payments, vendors? | "no" | General statement; no control deviation | Third self-reported "no concerns" across three different respondents; Qplanning_13 conflict (0.981) |
| Q188 | % split of distributor end customers — private vs public? | "Not responsible — refer to Mark Howard" | Informational; referral | Critical question left unanswered — this directly feeds the 81% wholesaler public-sector exposure concern |
| Q226 | Biggest distributor-related risks? | "N/A" | Perception only; no control | N/A response to a directly relevant question — no follow-up |
| Q228 | Challenges with distributor compliance? | "N/A" | General perspective; no control content | N/A despite 81% wholesaler sales concentration |
| Q236 | Do distributors interact with HCPs/GOs on our behalf? | "Not responsible — refer to Mark Howard" | No indication of interaction | Key DD question left unanswered — referral does not constitute a No Risk result |
| Q277 | How often do you interact with HCCO? Type of discussions? | "On an as-needed basis; when relevant in Leadership Team meetings" | Ad hoc engagement aligns with policy | Ad hoc HCCO engagement is insufficient for a company where 81% of sales go through wholesalers with a repeat DD finding |

### Justification
The questionnaire mapped 13 interview questions to this area. Three different respondents all said "no concerns" on FCPA/corruption, and two respondents deflected key distributor questions to a third party ("refer to Mark Howard"). These are not No Risk confirmations — they are unanswered questions. The transactional layer correctly found undisclosed TPIs (Row 428) and the USD 562.9M one-time vendor bypass (Row 466). Both FRR findings should be Potential Risk: undisclosed TPIs are the exact repeat CPR finding the scoping deck identified, and USD 562.9M in uncontrolled HCP/HCO disbursements is a systemic control failure.

---

## 8. Expense Reports
**Scoping Deck Status:** OUT OF SCOPE | Explicitly excluded

### Scoping Deck Rationale for Exclusion
- **Noted risk:** Duplicate transactions, HCP meals over limit, HCP/lodging paid in cash
- Critical finding from prior CPR; SI activity
- A matter was escalated locally (did not meet escalation criteria) — duplicate transactions identified
- T&E Red Flags training will be provided onsite
- **Excluded because:** Monitoring by local team; doesn't meet escalation criteria

### System Verdict
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Questionnaire | Q70 | No Risk | General corruption concern — mention of KOL engagements in oncology (Carvykti) but no expense control deviation |
| Questionnaire | Q357 | No Risk | Performance deviation question — commercial context only |
| Transactional | 438 | **Potential Risk** 🔴 | 1,990 HCP/GO lunches; 214 dinners; 8 symposium/sponsorship items — repeated per-attendee threshold breaches; split claims to bypass limits; gifts above policy limits by named employees |

### Overlap / Gap Status
**🔴 ESCALATION REQUIRED** — Scoping deck acknowledged risk but excluded the area. The system confirmed Potential Risk through transactional evidence. The exclusion rationale (monitoring by local team, escalation criteria not met) does not override a finding of 1,990+ HCP/GO meals with per-attendee threshold breaches and deliberate split claims.

### Question Mappings

| Q# | Question | Answer | System Reason for No Risk | Gap Explanation |
|---|---|---|---|---|
| Q70 | Any FCPA concerns — payments, vendors? | "Not specifically, although we do seem to utilise a handful of KOLs in oncology (Carvykti) for market access" | General scoping input; no expense control deviation | Respondent mentioned KOL usage — this is an HCP engagement risk signal that was not followed up |
| Q357 | Significant deviation from industry benchmarks? | "Deviations mostly driven by market growth, price or product launch timing" | Commercial performance only | Off-topic for expense reports |

### Justification
Expense Reports was excluded from scope because the local team was monitoring it. However, the system's transactional finding shows the monitoring did not prevent 1,990+ threshold-breaching meals, split claims, and gifts above limits. The scoping deck's own comment — "some risk related to duplicate transactions, HCP meals over limit" — is confirmed by the system. The prior CPR critical finding and SI activity also support re-inclusion. Recommend raising this Potential Risk to scoping deck stakeholders.

---

## 9. Market Research
**Scoping Deck Status:** OUT OF SCOPE | Reviewed under Third-Party Payments & DD

### Scoping Deck Rationale
- Multiple market research transactions miscoded to wrong GL (Other Purchased Goods & Services vs Mkt Research Other); HCCO confirmed 2 payments miscoded
- **Out of scope because:** GA&A will review market research TPIs and payments as part of Third-Party Payments & DD

### System Verdict
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Transactional | 464 | **FRR** ⚠️ | 19 market-research–tagged AP payments (USD 144,023.66) — every payment carries HCP/GO indicators; 12 of 19 lack PO numbers (weak SOW linkage); 2 payments ≥ USD 20,000 without HCCO flags |

### Overlap / Gap Status
**⚠️ ALIGNED — Out of scope correctly, but FRR finding needs folding into DD review.** The scoping deck planned to cover market research under Third-Party Payments & DD. The system's FRR (HCP signals + missing POs) confirms the scoping deck's GL miscoding concern and should be carried forward into the DD scope area review.

### Justification
All 19 market research payments carried HCP/GO indicators, which means every transaction had potential improper influence exposure. Missing POs on 12 of 19 means the payments had no approved contract/SOW linkage — a direct control gap. This FRR should be presented alongside the DD findings (Rows 428 and 466) as part of the Third-Party Payments scope.

---

## 10. Political & Cash Donations
**Scoping Deck Status:** OUT OF SCOPE | Explicitly excluded

### Scoping Deck Rationale
**Political Donations:** In June 2024, monitoring identified lack of documentation of process and support (agreement, FMV, etc.); MAP remediation due March 2025. Out of scope due to IM US monitoring and low quantity.
**Cash Donations:** Managed/approved at ANZ enterprise level (Innovative Medicine contributes 46.9%); actuals cross-charged. Out of scope due to enterprise-level approvals and low quantity.

### System Verdict
| Source | Row | Verdict | Finding |
|---|---|---|---|
| Transactional | 437 | **FRR** ⚠️ | Donation-related AP payments clustered in last 5 days of multiple months (period-end ratios: ~45% Jan 2024, ~33–37% Aug/Nov 2024); recipients include public hospitals and universities |

### Overlap / Gap Status
**⚠️ NOTE — FRR found despite exclusion.** The scoping deck excluded this area because of active monitoring and a MAP in remediation. The system's period-end clustering to public institutions is a data pattern worth noting — clustering around month-end to government-linked recipients is a known red flag for improper influence timing.

### Justification
The scoping deck's own note ("lack of documentation of process and support; MAP remediation due March 2025") confirms the control gap was already known. The system's period-end clustering pattern adds a transactional dimension. Since a MAP is already open, this FRR should be tracked against the MAP remediation rather than treated as a new standalone finding.

---

## Summary Table

| Scoping Deck Area | Deck Status | System Verdict | Match | Key Gap / Overlap |
|---|---|---|---|---|
| Educational Grants | IN SCOPE | **Potential Risk** ✅ | ✅ Overlap | Row 434: 4 grants approved after event start — confirmed control breach matching scoping deck concern. Row 443 (IIS) is a system-only finding — IIS not mentioned in scoping deck |
| Fee for Service | IN SCOPE | **No Risk** | ❌ Gap | Zero FFS rows in HCC dataset — data feed failure; all questions were generic awareness prompts |
| Congresses & JJ Events | IN SCOPE | **No Risk** | ❌ Gap | Zero congress rows in HCC dataset — data feed failure; only 1 generic question mapped |
| Sponsorships | IN SCOPE | **FRR** | ⚠️ Partial | Spend concentration + doc gaps found; repeat CPR history warrants Potential Risk |
| Tenders | IN SCOPE | **FRR** | ⚠️ Partial | Credit memos to public hospitals post-award; only 1 narrow question (participation fees) tested |
| Patient Support Programs | IN SCOPE | **FRR** | ⚠️ Partial | Monash University payment + unregistered admins; Q47 respondent named PSP as biggest risk — not cross-referenced |
| Third-Party Payments & DD | IN SCOPE | **FRR × 2** | ⚠️ Partial | USD 562.9M one-time vendor bypass + undisclosed TPIs; 13 questions mapped, multiple deflected to "refer to Mark Howard" |
| Expense Reports | OUT OF SCOPE | **Potential Risk** | 🔴 Escalate | 1,990+ HCP/GO meals over limit; split claims confirmed — scoping deck acknowledged risk and excluded anyway |
| Market Research | OUT OF SCOPE | **FRR** | ⚠️ Aligned | All 19 payments have HCP signals + missing POs — should fold into DD review per scoping deck plan |
| Political & Cash Donations | OUT OF SCOPE | **FRR** | ⚠️ Note | Period-end clustering to public institutions; MAP already open — track against remediation |

---

## 11. System Assessment — What Is Working, What Is Missing, and What We Communicate to the Client

---

### 11A. What the Current System Is Doing Correctly

| Capability | Evidence |
|---|---|
| **Transactional pattern detection** | Correctly identified approval-after-event-start in Educational Grants (Row 434); found Monash University payment to a public institution in PSP (Row 433); detected credit memo anomalies in Tenders (Row 432) and one-time vendor bypass at scale (Row 466) |
| **Scope area assignment** | System correctly maps questionnaire responses and transaction data to specific FCPA control domains (e.g., EDUCATIONAL_GRANTS, SPONSORSHIPS, DUE_DILIGENCE) |
| **FRR classification** | Where transactional red flags exist but cannot be auto-confirmed, the system correctly holds at FRR rather than fabricating a verdict — conservative and defensible |
| **Cross-question conflict detection** | System flagged similarity conflicts (e.g., Q30/Q110/Q162 all matching Qplanning_13 at similarity 0.975–1.000) — indicating repeated denial answers across interviews that may signal coached or coordinated responses |
| **Data-driven No Risk verdicts** | Where data genuinely showed no risk (e.g., Lobbyists, Contract Sales Agents, Local Travel Agencies, Audit Fees) the system correctly returned No Risk with clear evidence — avoiding false positives |
| **Out-of-scope escalation** | System correctly surfaced Expense Reports as Potential Risk (Row 438) even though it was excluded from scope — showing it can identify risk beyond declared boundaries |

---

### 11B. What the Current System Is NOT Doing — Key Gaps

#### Gap 1: System Does Not Use Prior CPR / Historical Findings as Risk Priors

The system treats each audit as standalone. It has no knowledge of prior CPR findings, repeat violations, or historical risk classifications. The scoping deck explicitly states:
- FFS: "Critical findings from prior CPR (including **repeat**)"
- Sponsorships: "Critical finding from prior CPR (**repeats**)"
- Third-Party Payments & DD: "Critical finding from prior CPR and **major repeat finding**"
- Educational Grants: "Critical findings from prior CPR"
- Congresses: "Critical finding from prior CPR"

When the system returns FRR for Sponsorships or Tenders, it does not know these are repeat violations. If it did, it would have the basis to escalate FRR → Potential Risk automatically for any area with a prior CPR flag. **The system is assessing risk in isolation; the scoping deck is assessing it with history.**

#### Gap 2: System Does Not Ingest GACS Reports

GACS (Global Audit & Compliance System) assessment reports are referenced in the scoping deck for multiple areas:
- Patient Support Programs: "GACS assessment identified critical issues"
- Fee for Service: "GACS assessment includes critical observations"
- Sponsorships: "Recent GACS assessment"

The system has no access to GACS report content. It cannot use GACS-identified issues as a risk baseline or corroborating signal. This means areas where GACS already confirmed problems are being re-assessed from scratch — and returning FRR or No Risk — simply because the current transactional dataset doesn't replicate what GACS found.

#### Gap 3: System Does Not Flag Risk from Missing Evidence

When data is absent (FFS: zero rows, Congresses: zero rows), the system returns **No Risk** — because it can only score what it sees. It does not distinguish between:
- "No transactions exist" → genuinely zero activity
- "No transactions returned" → data feed failure or population extraction gap

The correct response when a high-priority in-scope area returns zero rows should be **"Unable to Assess — Data Missing"** not **"No Risk Identified."** The current verdict creates a false sense of clearance for areas the scoping deck has already identified as high-risk based on external evidence.

#### Gap 4: System Does Not Cross-Reference Questionnaire Signals with Transactional Findings

At Q47, a respondent said *"PSP because of the nature of the activity"* is the biggest HCC risk. The system classified this as "general risk views; no specific control assessed" and gave No Risk. Separately, Row 433 found a USD 190,074 PSP payment to a public university and returned FRR. These two signals were never combined. A cross-referencing layer would have elevated PSP from FRR to Potential Risk — the respondent's own words corroborate the transactional anomaly.

#### Gap 5: System Does Not Incorporate Finance Director or MD Risk Flags

The scoping deck records qualitative risk inputs from the Finance Director (FFS: "noted as high-risk area by FD") and Managing Director (PSP: "highlighted as a risk area by MD"). These are senior stakeholder risk signals that the system has no mechanism to ingest. A case where both the FD and MD have flagged an area, and the GACS assessment found critical issues, but the system still returns FRR is a calibration problem — not a system success.

#### Gap 6: System Cannot Assess Controls That Require Document Review

Several scoping deck risks require reviewing actual documents:
- FFS: Were consulting contracts in place? Was FMV documentation on file?
- Congresses: Were post-event documentation forms retained? Were CVENT exception requests properly replaced by contracts?
- Sponsorships: Were HCCO approvals obtained per transaction?

The system can detect missing patterns in structured data but cannot open, read, or evaluate PDF contracts, approval letters, or event documentation. These gaps will not be surfaced by the AI system and require manual sample review.

---

### 11C. What We Can Do Further — Communication to Client

#### Points We Are Aligned On (Confirmed Overlaps)

These are areas where the system's findings directly match what the scoping deck identified as risk. These can be presented to the client as validated findings:

| Area | Scoping Deck Risk | System Confirmation | Message to Client |
|---|---|---|---|
| **Educational Grants** | HCCO approvals not obtained when required; prior CPR critical finding | Row 434: 4 grants approved after event start (Leukaemia Foundation, ANZ Pulmonary Hypertension Society) | Confirmed. Pre-approval control is failing. Recommend immediate sample review of all grant approvals in scope period to quantify full population |
| **Educational Grants / IIS** | High average grant value; prior CPR findings | Row 443: Paid amounts exceed approved amounts; approval-sequence anomalies to public institutions | Partially confirmed (FRR). Paid > approved on grants to public hospitals/universities is a direct HCBI control breach. Escalate to Potential Risk |
| **Third-Party Payments & DD** | Major repeat finding; DD gaps; 81% wholesaler sales | Row 428: Suppliers with TPI keywords not in FIRST inventory (undisclosed TPIs) | Confirmed pattern. These suppliers may be unregistered TPIs — directly matches the repeat CPR finding. Verify FIRST registration and exemption status |
| **Third-Party Payments & DD** | TPI management gaps; one-time vendor misuse | Row 466: USD 562.9M in HCP/HCO payments via one-time vendor route (no VENDOR_ID; 661 distinct supplier names) | Confirmed. Scale is extremely material. One-time vendor route is being used to bypass FIRST DD for HCP/HCO payments |
| **Sponsorships** | Repeat CPR; GACS assessment; FD risk flag | Row 431: Concentrated spend to public associations; documentation gaps | Signal confirmed. Recommend escalating to Potential Risk given prior CPR repeat history and GACS confirmation |
| **Tenders** | Prior CPR; 80% public tender exposure | Row 432: Large credit memos to public hospitals post-award with zero discount pattern | Signal confirmed. Post-award credit memos to government hospitals without discount rationale warrant sample review |
| **Patient Support Programs** | GACS critical issues; Legal recommendation; SI activity | Row 433: USD 190,074 to Monash University (public HCO); AESIR fee spike; unregistered PSP admins | Signal confirmed. Recommend verifying HCBI pre-approval for Monash University payment and FIRST DD status of PSP administrators |
| **Expense Reports** | Prior CPR; SI activity; duplicate payments; HCP meals over limit | Row 438: 1,990+ HCP/GO meals over per-attendee limits; split claims confirmed | Confirmed beyond scope boundary. Recommend re-opening Expense Reports for at least a targeted sample review |

#### Points We Are Getting (FRR — Require Client Confirmation to Close)

These findings exist in the data but the system cannot auto-confirm without additional input. The client needs to provide documentation or clarification to resolve each:

| Area | FRR Finding | What Client Needs to Provide |
|---|---|---|
| Sponsorships | High-concentration spend to public associations; doc gaps | HCCO approval records for top 3 recipients (Australasian College of Dermatology, The Conference Company, Urological Society of Australia) |
| Tenders | Credit memos to public hospitals post-award | Tender contract terms showing credit memo basis for Austin Hospital, Royal Hobart, Peter MacCallum, Waikato DHB |
| Patient Support Programs | Monash University payment; AESIR fee spike | HCBI pre-approval for Monash University PSP payment; FIRST registration status for AESIR HEALTH and other PSP administrators |
| Third-Party Payments & DD | Undisclosed TPIs (SOUTHERN CROSS MEDICAL CARE, CROHN'S COLITIS CURE, etc.) | FIRST TPI inventory entries or exemption decisions for these suppliers |
| Third-Party Payments & DD | USD 562.9M one-time vendor HCP/HCO payments | Vendor master detail and FIRST DD completion evidence for top suppliers in the null-VENDOR_ID population |
| Market Research | All 19 payments carry HCP signals; 12 missing POs | Approved SOW or contract for each of the 12 PO-missing market research payments |
| IIS / Research Grants | Paid amounts exceed approved amounts | HCC approval records showing amended approved amounts or milestone structure for flagged grants |
| Donations | Period-end clustering to public hospitals/universities | MAP status and remediation evidence (MAP due March 2025); disbursement schedule rationale |

#### Points the Client Has Identified That We Are Missing (Gaps to Acknowledge)

These are scoping deck-identified risks where the system produced No Risk due to data feed failures or question design gaps. The client should be informed that these are **untested, not cleared**:

| Area | Scoping Deck Risk | System Result | What to Tell Client |
|---|---|---|---|
| **Fee for Service** | Repeat CPR finding; HCC Committee confirmed duplicate payments and inadequate documentation in Q1 2024; GACS critical observations; FD high-risk flag | No Risk — zero FFS rows in HCC dataset | The FFS population was not captured in the audit dataset. This area is untested, not cleared. Recommend obtaining HCC event extract specifically for FFS consulting engagements and re-running |
| **Congresses & JJ Events** | Prior CPR critical finding; 5 GL-miscoded transactions confirmed; post-event documentation not retained; MAP opened for CVENT exception process | No Risk — zero congress rows in HCC dataset | Same data issue as FFS. AUD 1.45M in confirmed congress spend exists per the scoping deck. The HCC event dataset did not return any congress records. Untested, not cleared |
| **Prior CPR / Historical Risk** | Scoping deck explicitly cites repeat findings across FFS, Sponsorships, DD, Congresses, Educational Grants | System has no prior CPR data | The system cannot weight findings by recurrence. Repeat violations carry higher inherent risk than first-time observations. The client should provide prior CPR finding summaries so the system can calibrate FRR → Potential Risk escalation thresholds |
| **GACS Reports** | GACS critical issues cited for PSP, FFS, Sponsorships | System has no GACS data | GACS assessments represent independent third-party validation of control failures. Where GACS has already confirmed a problem, the system's FRR verdict underweights the risk. Recommend sharing GACS summaries for system ingestion |
| **Finance Director / MD Risk Flags** | FD flagged FFS and Sponsorships as high-risk; MD flagged PSP | System has no qualitative stakeholder risk input | Senior leadership risk flags should be incorporated as qualitative priors. Where FD or MD has named an area as high-risk, the system should treat FRR findings in that area as Potential Risk by default |
