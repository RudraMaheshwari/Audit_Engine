"""
Generates AUDIT_DEMO_SCENARIOS.docx
Run: python docs/generate_demo_doc.py
"""
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

doc = Document()

for section in doc.sections:
    section.top_margin    = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin   = Cm(2.2)
    section.right_margin  = Cm(2.2)

# ── Colours ───────────────────────────────────────────────────────────────────
DARK_BLUE  = RGBColor(0x1F, 0x49, 0x7D)
MID_BLUE   = RGBColor(0x2E, 0x74, 0xB5)
TEAL       = RGBColor(0x17, 0x6D, 0x83)
RED_RISK   = RGBColor(0xC0, 0x00, 0x00)
BLACK      = RGBColor(0x00, 0x00, 0x00)
LABEL_BG   = "2E74B5"
VALUE_BG   = "FCE4D6"
CODE_BG    = "F2F2F2"
INPUT_LBL  = "D6DCE4"   # grey for input table labels


def _shd(cell, hex_color):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_color)
    tcPr.append(shd)


def _borders(cell, color="BFBFBF"):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    b    = OxmlElement("w:tcBorders")
    for s in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{s}")
        el.set(qn("w:val"),   "single")
        el.set(qn("w:sz"),    "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        b.append(el)
    tcPr.append(b)


def _set_col_width(table, col_idx, width_cm):
    from docx.oxml.ns import qn as _qn
    from docx.shared import Cm as _Cm
    for row in table.rows:
        cell = row.cells[col_idx]
        tc   = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcW  = OxmlElement("w:tcW")
        tcW.set(_qn("w:w"),    str(int(_Cm(width_cm) / 914.4 * 1440)))
        tcW.set(_qn("w:type"), "dxa")
        tcPr.append(tcW)


# ── Heading helpers ───────────────────────────────────────────────────────────
def h_title(text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(16); r.font.color.rgb = DARK_BLUE


def h_scenario(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after  = Pt(4)
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(14); r.font.color.rgb = DARK_BLUE


def h_phase(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(4)
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(13); r.font.color.rgb = DARK_BLUE


def h_section(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(2)
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(11); r.font.color.rgb = MID_BLUE


def h_check(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after  = Pt(4)
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(11); r.font.color.rgb = TEAL


def italic_note(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(6)
    r = p.add_run(text)
    r.italic = True; r.font.size = Pt(9.5); r.font.color.rgb = RGBColor(0x40, 0x40, 0x40)


def bold_label(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after  = Pt(1)
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(10); r.font.color.rgb = BLACK


def bullet(text, bold_prefix=""):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after  = Pt(1)
    if bold_prefix:
        rb = p.add_run(bold_prefix); rb.bold = True; rb.font.size = Pt(10)
    r2 = p.add_run(text); r2.font.size = Pt(10)


def sub_bullet(text):
    p = doc.add_paragraph(style="List Bullet 2")
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after  = Pt(1)
    r = p.add_run(text); r.font.size = Pt(10)


def placeholder(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(6)
    p.paragraph_format.left_indent  = Cm(0.5)
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "FFF2CC")
    pPr.append(shd)
    r = p.add_run(text); r.italic = True; r.font.size = Pt(9); r.font.color.rgb = RGBColor(0x7F, 0x60, 0x00)


def code_block(text):
    """Monospace grey block — uses table cell so text wraps properly."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.style = "Table Grid"
    cell = tbl.rows[0].cells[0]
    _shd(cell, CODE_BG)
    _borders(cell, "BFBFBF")
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(2)
    r = p.add_run(text)
    r.font.name = "Courier New"; r.font.size = Pt(8); r.font.color.rgb = RGBColor(0x26, 0x26, 0x26)
    doc.add_paragraph()


def input_table(fields: list[tuple[str, str]]):
    """2-column table for questionnaire input — label | value. Text wraps."""
    tbl = doc.add_table(rows=len(fields), cols=2)
    tbl.style = "Table Grid"
    tbl.autofit = False
    for i, (label, value) in enumerate(fields):
        lc = tbl.rows[i].cells[0]
        vc = tbl.rows[i].cells[1]
        _shd(lc, INPUT_LBL)
        _shd(vc, CODE_BG)
        _borders(lc); _borders(vc)
        # label
        lp = lc.paragraphs[0]
        lr = lp.add_run(label)
        lr.font.name = "Courier New"; lr.font.size = Pt(8.5)
        lr.bold = True; lr.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)
        # value
        vp = vc.paragraphs[0]
        vr = vp.add_run(value)
        vr.font.name = "Courier New"; vr.font.size = Pt(8.5)
        vr.font.color.rgb = RGBColor(0x26, 0x26, 0x26)
    # column widths: label 3cm, value 14cm
    for row in tbl.rows:
        for ci, w in enumerate([3.0, 14.0]):
            cell = row.cells[ci]
            tc   = cell._tc
            tcPr = tc.get_or_add_tcPr()
            tcW  = OxmlElement("w:tcW")
            tcW.set(qn("w:w"),    str(int(Cm(w).pt * 20)))
            tcW.set(qn("w:type"), "dxa")
            tcPr.append(tcW)
    doc.add_paragraph()


def risk_table(rows, label_bg=LABEL_BG, value_bg=VALUE_BG, label_color=None):
    tbl = doc.add_table(rows=len(rows), cols=2)
    tbl.style = "Table Grid"
    tbl.autofit = False
    lbl_rgb = label_color or RGBColor(0xFF, 0xFF, 0xFF)
    for i, (label, value) in enumerate(rows):
        lc = tbl.rows[i].cells[0]
        vc = tbl.rows[i].cells[1]
        _shd(lc, label_bg); _shd(vc, value_bg)
        _borders(lc); _borders(vc)
        lr = lc.paragraphs[0].add_run(label)
        lr.bold = True; lr.font.size = Pt(9); lr.font.color.rgb = lbl_rgb
        vr = vc.paragraphs[0].add_run(value)
        vr.font.size = Pt(9.5)
    for row in tbl.rows:
        for ci, w in enumerate([4.5, 12.5]):
            cell = row.cells[ci]
            tc   = cell._tc
            tcPr = tc.get_or_add_tcPr()
            tcW  = OxmlElement("w:tcW")
            tcW.set(qn("w:w"),    str(int(Cm(w).pt * 20)))
            tcW.set(qn("w:type"), "dxa")
            tcPr.append(tcW)
    doc.add_paragraph()


def row_written(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(8)
    r = p.add_run(text)
    r.bold = True; r.font.size = Pt(10); r.font.color.rgb = RED_RISK


def hr():
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(6)
    pPr = p._p.get_or_add_pPr()
    pb  = OxmlElement("w:pBdr")
    bot = OxmlElement("w:bottom")
    bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "6")
    bot.set(qn("w:space"), "1"); bot.set(qn("w:color"), "BFBFBF")
    pb.append(bot); pPr.append(pb)


def page_break():
    doc.add_page_break()


# ══════════════════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════════════════
h_title("AI Auditor  —  Live Run Demo Scenarios")
doc.add_paragraph()


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1  —  Row 288
# ══════════════════════════════════════════════════════════════════════════════
h_scenario("SCENARIO 1  —  POTENTIAL RISK   (Policy & Document Cross-Validation)")
hr()

h_section("Input  —  Questionnaire Data")
input_table([
    ("row_id",              "288"),
    ("questionnaire_type",  "Planning"),
    ("processed_at",        "2026-08-23 17:47:40"),
    ("question",            "For the Due Diligence Process, please provide the following:\n"
                            "a. Current TPI inventory, including all intermediaries used by the Company.\n"
                            "b. List of any red flags that are still under mitigation and a brief description of the red flag.\n"
                            "Please ensure the TPI list contains a description of the TPI's responsibility\n"
                            "(e.g., distributor, promotional sales agent, lobbyists)."),
    ("response",            "File Tab 4 Item 3"),
    ("followup_question",   "1. Can you please confirm why Nexus Australasia Pty Ltd Trading as Zest Healthcare\n"
                            "   Communications has no red flags listed? HR provided support that a COI disclosure\n"
                            "   for Patient Solutions Manager (Helena Jejina) was made. Disclosed on 6/12/2024.\n"
                            "2. Can you please provide a listing of all Wholesalers?\n"
                            "3. What steps have been taken to resolve the red flag resulting from reputational\n"
                            "   checks for Aspen Pharmacare Australia Pty Ltd?"),
    ("followup_response",   "1. At the time Due Diligence was undertaken (initiated 11 November, Final approval\n"
                            "   14 November 2024) there were no red flags — completed prior to Helena's\n"
                            "   disclosure/employment. Helena declared the conflict; reviewed with legal.\n"
                            "2. Wholesalers: Aust. Pharmaceutical Industries Ltd (API), Symbion Pty Ltd,\n"
                            "   Clifford Hallam Healthcare Pty Ltd (CH2) and Sigma Company Ltd.\n"
                            "3. Legal reviewed reputational checks re competition per attached (Tab 4 Item 3\n"
                            "   email) — see legal response on mitigations related to contract terms."),
])

h_section("Routing Decision")
bullet("No financial figures, currency symbols or payment amounts detected in the response or follow-up")
bullet("Routes to all three Policy & Document Readers simultaneously (parallel lookup):")
sub_bullet("J&J Global Compliance Policy Reader  (HCBI Guide)")
sub_bullet("TPI Due Diligence Policy Reader  (Local TPI Policies)")
sub_bullet("Evidence & Document Reader  (Supporting Documents)")
bullet("Follow-up response names four wholesalers — triggers inventory cross-check in document readers")
bullet("NOT invoked: ", "Transactional Data Agent")

h_section("Policy & Document Readers  —  What Was Found")
bold_label("J&J Global Compliance Policy Reader  (HCBI Guide — TV-POL-02257_v3.0.pdf)")
bullet("Section 10.2.1 — Inventory of Third-Party Relationships: J&J entities must maintain a complete, "
       "up-to-date inventory of all in-scope TPI relationships, including scope determinations and "
       "due diligence status for each entity.")
bold_label("TPI Due Diligence Policy Reader")
bullet("Local policy confirms all intermediaries — including wholesalers — must be assessed for in-scope "
       "status and listed in the TPI inventory with supporting due diligence documentation.")
bold_label("Evidence & Document Reader")
bullet("Tab 4 Item 3.xlsx (Sheet1): TPI inventory file provided — the four wholesalers named in the "
       "follow-up (API, Symbion, CH2, Sigma) do NOT appear in this file.")
bullet("TPI_Inventory_Final_Output.xlsx: The four named wholesalers are also absent from this file.")
bullet("No scope-out determinations or documented rationale for their exclusion found in either file.")
placeholder("[ SCREENSHOT — Tab 4 Item 3.xlsx showing inventory entries with wholesalers absent ]")

h_section("Compliance Gap Detector  —  Findings")
bullet("Management confirmed four active wholesalers in follow-up: ", "API, Symbion, CH2, Sigma")
bullet("Neither provided inventory file contains entries for any of these four entities")
bullet("No scope-out determination or documented exclusion rationale present for any of the four")
bullet("Gap: inventory population completeness — management-identified intermediaries unaccounted for in TPI inventory")
bullet("Transactional intent: Not detected — Transactional Data Agent not invoked")

h_section("Audit Risk Decision Maker  —  Output")
risk_table([
    ("risk_title",             "Potential Risk"),
    ("risk_type",              "Wholesalers not reconciled to TPI inventory; scope status requires verification"),
    ("risk_category",          "Medium Risk  (Score: 6 / 10)"),
    ("scope_area",             "TPI Due Diligence"),
    ("risk_description",       "Completeness of the TPI inventory could not be determined because wholesalers "
                               "identified by management (API, Symbion, CH2, Sigma) do not appear in the provided "
                               "TPI inventory files, and no scope determinations or due diligence records were "
                               "provided to confirm whether they are in-scope TPIs that should be included."),
    ("risk_rationale",         "Under the HCBI Guide's TPI management requirements, an up-to-date inventory of TPI "
                               "relationships must be maintained (TV-POL-02257_v3.0, Section 10.2.1). Management "
                               "listed four wholesalers in the follow-up, but the provided inventory files (Tab 4 "
                               "Item 3.xlsx; TPI_Inventory_Final_Output.xlsx) do not show these entities. Evidence "
                               "is Insufficient to Test whether these wholesalers meet in-scope criteria."),
    ("risk_category_reasoning","Score 6 assigned under the Medium band (5-7). A credible and material unresolved "
                               "issue exists regarding population completeness and scope determinations, but "
                               "non-compliance cannot be confirmed from the available evidence."),
    ("source_details",         "- Source: HCBI Guide (Global Policy); File Name: TV-POL-02257_v3.0.pdf;\n"
                               "  Section: 10.2.1 Inventory of Third-Party Relationships; Page No.: Not Available\n\n"
                               "- Source: Supporting Evidence; File Name: Tab 4 Item 3.xlsx;\n"
                               "  Section: Sheet1; Page No.: Not Available\n\n"
                               "- Source: Supporting Evidence; File Name: TPI_Inventory_Final_Output.xlsx;\n"
                               "  Section: Not Available; Page No.: Not Available"),
])
placeholder("[ SCREENSHOT — Excel output row 288 showing the above fields populated ]")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2  —  Row 303
# ══════════════════════════════════════════════════════════════════════════════
page_break()
h_scenario("SCENARIO 2  —  POTENTIAL RISK   (Policy & Document Cross-Validation)")
hr()

h_section("Input  —  Questionnaire Data")
input_table([
    ("row_id",             "303"),
    ("questionnaire_type", "Planning"),
    ("processed_at",       "2026-08-23 17:57:45"),
    ("question",           "Does your company engage TPI to conduct marketing activities that involve HCPs?\n"
                           "If yes, please list these activities and provide information such as a brief overview,\n"
                           "the main contact person who oversees the activities, a summary of the amount of money\n"
                           "spent, the name of the vendors paid, the name of the HCPs involved, etc."),
    ("response",           "See File Tab 4 Item 19.\n"
                           "Note, we do not record the HCP details, as it is the TPIs who market/sell to HCPs\n"
                           "(it is not something we require the TPI to document every time they promote/market\n"
                           "to an HCP). The HCPs are not involved in performing the marketing on our behalf."),
    ("followup_question",  "1. Are there contractual terms in place which give J&J the right to check that all\n"
                           "   information shared by Aspen/DKSH/MCI Australia during promotional events is still\n"
                           "   up-to-date and in accordance with the currently approved product information?"),
    ("followup_response",  "1. Under the MSA for each Supplier, the Supplier must:\n"
                           "   - ensure Services are supplied in accordance with the Agreement and SOW;\n"
                           "   - comply with any reasonable directions given by Customer from time to time;\n"
                           "   - provide all services in compliance with all applicable laws and guidelines.\n\n"
                           "   For Aspen, clause 7 of the Distribution and Promotion Agreement includes:\n"
                           "   - Promotional Materials to be reviewed and approved by Janssen prior to use;\n"
                           "   - Compliance with Medicines Australia Code and all applicable laws.\n\n"
                           "   Janssen also has the right (cl 7.5) to ask Aspen to cease using any Promotional\n"
                           "   Materials at any time and Aspen must update if Janssen notifies of any change."),
])

h_section("Routing Decision")
bullet("No financial figures, currency symbols or payment amounts detected in the response or follow-up")
bullet("Routes to all three Policy & Document Readers simultaneously (parallel lookup):")
sub_bullet("J&J Global Compliance Policy Reader  (HCBI Guide)")
sub_bullet("TPI Due Diligence Policy Reader  (Local TPI Policies)")
sub_bullet("Evidence & Document Reader  (Supporting Documents)")
bullet("Follow-up names three vendors (Aspen, DKSH, MCI Australia) — triggers cross-check across all three")
bullet("NOT invoked: ", "Transactional Data Agent")

h_section("Policy & Document Readers  —  What Was Found")
bold_label("J&J Global Compliance Policy Reader  (HCBI Guide — TV-POL-02257_v3.0.pdf)")
bullet("Section 10.2 — Management of Third-Party Intermediaries (Page 73): J&J must actively manage "
       "TPIs including testing, monitoring, and maintaining adequate documentation of TPI activities.")
bullet("Sections 10.2.10 and 10.2.11 (Page 77): Requirements for testing/monitoring of TPI activities "
       "and documentation of TPI management must be maintained.")
bold_label("TPI Due Diligence Policy Reader")
bullet("Local policy confirms HCP-facing TPIs require enhanced oversight, including contractual controls, "
       "monitoring plans, and records substantiating compliance with approved product information.")
bold_label("Evidence & Document Reader")
bullet("Tab 4 Item 19: Referenced by management but file was not available for review — content could not be assessed.")
bullet("Tab 4 Item 40 — Distributor Agreement (Aspen): Clause 7 and 7.5 confirm Janssen's approval rights "
       "and cessation rights over Promotional Materials for Aspen only.")
bullet("No comparable contractual documentation provided for DKSH or MCI Australia.")
bullet("No monitoring records or logs evidencing what was shared with HCPs across any vendor were provided.")
placeholder("[ SCREENSHOT — Tab 4 Item 40 Distributor Agreement showing Clause 7 / 7.5 ]")

h_section("Compliance Gap Detector  —  Findings")
bullet("Three HCP-facing TPIs identified: ", "Aspen, DKSH, MCI Australia")
bullet("Aspen: contractual oversight partially evidenced (Clause 7/7.5 of Distribution Agreement only)")
bullet("DKSH and MCI Australia: no comparable contractual controls provided")
bullet("Management states HCP interaction details are not recorded — monitoring records unavailable across all vendors")
bullet("Referenced file Tab 4 Item 19 was unavailable — cannot assess activity-level detail")
bullet("Gap: oversight and documentation for HCP-facing TPIs cannot be validated across the full vendor population")
bullet("Transactional intent: Not detected — Transactional Data Agent not invoked")

h_section("Audit Risk Decision Maker  —  Output")
risk_table([
    ("risk_title",             "Potential Risk"),
    ("risk_type",              "Oversight and documentation for HCP-facing TPIs cannot be validated across vendors"),
    ("risk_category",          "Medium Risk  (Score: 5 / 10)"),
    ("scope_area",             "TPI Due Diligence"),
    ("risk_description",       "Compliance with required oversight and documentation for TPIs conducting HCP-facing "
                               "marketing cannot be determined. Aspen's agreement evidences prior approval and "
                               "cessation rights over promotional materials, but comparable contractual controls for "
                               "DKSH/MCI and records or monitoring that verify information shared with HCPs were not "
                               "provided, and management states HCP interaction details are not recorded."),
    ("risk_rationale",         "Under the HCBI Guide, TPIs must be managed with testing/monitoring and adequate "
                               "documentation (Sections 10.2, 10.2.10, 10.2.11). The Aspen Distribution and Promotion "
                               "Agreement supports promotional material oversight for one TPI only. Comparable "
                               "DKSH/MCI terms and evidence of monitoring plans were not provided. The referenced "
                               "File Tab 4 Item 19 was unavailable. Evidence is Insufficient to Test whether required "
                               "monitoring and documentation are in place across all relevant TPIs."),
    ("risk_category_reasoning","Score 5 assigned under the Medium band (Further Review Required, 3-6). HCP-facing "
                               "intermediary activity presents a material risk indicator, but only partial evidence "
                               "(Aspen controls) was available and broader monitoring/documentation could not be verified."),
    ("source_details",         "- Source: HCBI Guide (Global Policy); File Name: TV-POL-02257_v3.0.pdf;\n"
                               "  Section: 10.2 Management of Third-Party Intermediaries; Page No.: 73; Ref: 10.2\n\n"
                               "- Source: HCBI Guide (Global Policy); File Name: TV-POL-02257_v3.0.pdf;\n"
                               "  Section: 10.2.10 Testing, Monitoring, Audits and 10.2.11 Documentation;\n"
                               "  Page No.: 77; Ref: 10.2.10-10.2.11\n\n"
                               "- Source: Supporting Evidence; File Name: Tab 4 Item 40 Distributor Agreement.pdf;\n"
                               "  Section: Clause 7 and 7.5 Promotional Materials; Page No.: Not Available"),
])
placeholder("[ SCREENSHOT — Excel output row 303 showing the above fields populated ]")


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3  —  Transactional Data Analysis
# ══════════════════════════════════════════════════════════════════════════════
page_break()
h_phase("PHASE 3  —  TRANSACTIONAL DATA ANALYSIS  (Runs independently, once per audit)")
italic_note(
    "Runs all 6 checks via SQL queries directly against 3 Databricks Delta tables and 2 reference files. "
    "Not tied to any single questionnaire question — produces its own risk rows in the output."
)

risk_table([
    ("Delta Table 1", "cat_cbt_gaa_brz_dev.default.payments_dashboard_summary\n"
                      "(TPI/vendor payment transactions — supplier, invoice amount, GL account, dates, PO)"),
    ("Delta Table 2", "cat_cbt_gaa_brz_dev.default.revenue_dashboard_credit_memo\n"
                      "(Credit memo and returns data)"),
    ("Delta Table 3", "cat_cbt_gaa_brz_dev.default.revenue_dashboard_summary\n"
                      "(Revenue / billing data — customer, billing net value, transaction type)"),
    ("Reference 1",   "TPI_Inventory_Final_Output.xlsx  (TPI name matching — approved TPI entities)"),
    ("Reference 2",   "HCC_GL_Account_Map-11142023.xlsx  (valid GL accounts — Lynx Account column)"),
], label_bg=LABEL_BG, value_bg="FFFFFF")

hr()

# ── Check 5 ───────────────────────────────────────────────────────────────────
h_check("Check 5  —  Invalid GL Account for TPI Payments  (RISK TRIGGERED)")

bullet("Logic: Cross-reference all TPI-coded payment transactions against the HCC GL Account Map "
       "(Lynx Account column). Flag any GL account code not present in the approved map.")
bullet("Result: 20 GL account codes found outside the HCC-approved map across 14,022 transactions "
       "totalling 1,368,331,424.28")

h_section("Row-Level Results  (calculation_evidence)")
code_block(
    "Payments Using GL Codes Not in HCC GL Account Map (all, ordered by amount)\n"
    "---------------------------------------------------------------------------\n"
    " 1. GL: 11352001 | Resrv Cust Rebates ChgP&L          | Txns:  242  | Amount: 717,443,649.08\n"
    " 2. GL: 21210009 | GR/IR Clearing Acct                | Txns: 1397  | Amount: 435,148,107.15\n"
    " 3. GL: 21291027 | VAT Payable                        | Txns:   14  | Amount: 123,729,012.60\n"
    " 4. GL: 63530044 | HCC/HCBI - Clinical Studies        | Txns: 2570  | Amount:  20,930,864.47\n"
    " 5. GL: 21510008 | International Tax On Income Stat.1 | Txns:    9  | Amount:  17,951,169.73\n"
    " 6. GL: 36630301 | Chrg Thru Value-RSU                | Txns:    5  | Amount:   7,311,384.09\n"
    " 7. GL: 62529040 | Other Purchased Goods & Services   | Txns:  440  | Amount:   6,450,934.20\n"
    " 8. GL: 21224006 | Empr Payroll Tax 2                 | Txns:   60  | Amount:   5,815,554.17\n"
    " 9. GL: 21291002 | Input Tax: Tax on Sales/Purchases  | Txns: 7380  | Amount:   5,504,988.33\n"
    "10. GL: 65990141 | IC Non LR41 Employee Expense ChrgIn| Txns:  265  | Amount:   3,891,553.87\n"
    "11. GL: 36630101 | Chrg Thru Value-SO                 | Txns:    5  | Amount:   3,853,238.95\n"
    "12. GL: 65027002 | Freight Expenses                   | Txns:  255  | Amount:   2,944,205.30\n"
    "13. GL: 65990105 | IC HR Charge-In                    | Txns:  736  | Amount:   2,886,350.21\n"
    "14. GL: 65549004 | SellingAids (Cons.Promo)           | Txns:  249  | Amount:   2,802,804.78\n"
    "15. GL: 62012001 | Dues Fees Subscription             | Txns:  165  | Amount:   2,455,391.76\n"
    "16. GL: 65010001 | Misc Freight (Stock & Shipping)    | Txns:   30  | Amount:   2,154,658.60\n"
    "17. GL: 65519912 | HCC/HCBI - Patient Support Programs| Txns:  159  | Amount:   1,957,485.66\n"
    "18. GL: 60517948 | Operating Margin Adjustment PY Aff | Txns:    1  | Amount:   1,883,453.00\n"
    "19. GL: 63530043 | HCC/HCBI - Investigator Init Stud. | Txns:   36  | Amount:   1,813,152.33\n"
    "20. GL: 21319044 | Fringe Benefit Tax                 | Txns:    4  | Amount:   1,403,466.00\n"
    "                                                                       -----------------------\n"
    "                                                        TOTAL          1,368,331,424.28"
)

h_section("RiskOutput Written  —  Check 5  (Row 431)")
risk_table([
    ("risk_title",             "Potential Risk"),
    ("risk_type",              "Invalid GL Account Usage — TPI Payments Outside HCC GL Map"),
    ("risk_category",          "Medium Risk  (Score: 6 / 10)"),
    ("scope_area",             "TPI Due Diligence"),
    ("risk_description",       "The automated control identified 14,022 TPI payment transactions totalling "
                               "1,368,331,424.28 posted to 20 GL account codes not included in the HCC approved GL "
                               "Account Map. High-value examples: Resrv Cust Rebates ChgP&L (717,443,649.08 / 242 "
                               "payments), GR/IR Clearing Acct (435,148,107.15 / 1,397 payments), VAT Payable "
                               "(123,729,012.60 / 14 payments). Recording TPI disbursements to unapproved GLs "
                               "indicates potential misclassification and can bypass HCC controls designed to "
                               "identify, pre-approve, and monitor healthcare-related spend routed through TPIs."),
    ("risk_rationale",         "Posting TPI-related payments outside the HCC-approved GL Account Map contravenes "
                               "HCC/HCBI requirements to use approved GLs for healthcare-related spend. This "
                               "undermines internal control principles of completeness and accuracy in financial "
                               "reporting and may result in incomplete HCBI transparency reporting and weakened "
                               "anti-bribery monitoring."),
    ("risk_category_reasoning","Score 6 assigned under the Medium band (5-7). Use of GL codes absent from the "
                               "HCC-approved map directly demonstrates a policy deviation across 14,022 transactions "
                               "totalling 1,368,331,424.28. Score capped at Medium pending confirmation of whether "
                               "misclassification was intentional or a data-entry error."),
    ("calculation_evidence",   "20 GL account code(s) used in TPI payments are not in the HCC approved GL Account Map.\n"
                               "Total value: 1,368,331,424.28 across 14,022 transactions.\n"
                               "(See row-level results above for full GL account breakdown.)"),
    ("source_details",         "- Source: Transactional Data Analysis — Check 5;\n"
                               "  Document: payments_dashboard_summary; Section: GL Account Validation\n\n"
                               "- Source: HCC GL Account Map;\n"
                               "  Document: HCC_GL_Account_Map-11142023.xlsx; Section: Approved GL Codes"),
])
row_written("ROW WRITTEN TO OUTPUT  [RED — Potential Risk, Score 6.0]")
placeholder("[ SCREENSHOT — Excel output row 431 showing Check 5 finding ]")

hr()

# ── Check 6 ───────────────────────────────────────────────────────────────────
h_check("Check 6  —  Payment to Unknown Supplier — Not in TPI Inventory  (RISK TRIGGERED)")

bullet("Logic: Match all supplier names in TPI-coded payment transactions against the TPI Inventory "
       "(fuzzy name matching). Flag any supplier with no match in the approved TPI Inventory.")
bullet("Result: 25 suppliers received TPI-coded payments totalling 1,352,213,059.08 across 6,524 "
       "transactions with no match in the approved TPI Inventory.")

h_section("Row-Level Results  (calculation_evidence)")
code_block(
    "Suppliers Receiving TPI Payments Not Found in TPI Inventory (all, ordered by amount)\n"
    "-------------------------------------------------------------------------------------\n"
    " 1. DEPARTMENT OF HEALTH & AGEING          | Txns:  258  | Amount:  718,962,474.62\n"
    " 2. JANSSEN-CILAG INTERNATIONAL NV         | Txns: 1376  | Amount:  434,758,398.53\n"
    " 3. AUSTRALIAN TAXATION OFFICE             | Txns:   33  | Amount:  141,263,780.58\n"
    " 4. J&J World Headquarters                 | Txns:   22  | Amount:   11,742,564.92\n"
    " 5. OFFICE OF STATE REVENUE NSW TREASUR    | Txns:   12  | Amount:    4,741,625.96\n"
    " 6. JOHNSON & JOHNSON MEDICAL PTY LTD      | Txns:  464  | Amount:    4,127,905.77\n"
    " 7. TEAM GLOBAL EXPRESS PTY LTD            | Txns:  534  | Amount:    3,238,550.39\n"
    " 8. DHL SUPPLY CHAIN (AUSTRALIA) PTY LTD   | Txns:   69  | Amount:    2,993,450.28\n"
    " 9. THERAPEUTIC GOODS ADMINISTRATION       | Txns:  147  | Amount:    2,580,822.60\n"
    "10. W130 JOHNSON & JOHNSON SERVICES        | Txns:   74  | Amount:    2,473,383.23\n"
    "11. ST VINCENTS HOSPITAL (MELBOURNE) LI    | Txns:  134  | Amount:    2,372,024.55\n"
    "12. IQVIA SOLUTIONS AUSTRALIA PTY LTD      | Txns:   48  | Amount:    2,204,673.58\n"
    "13. AMERICAN EXPRESS INTERNATIONAL INC     | Txns:  657  | Amount:    2,049,610.35\n"
    "14. MONASH UNIVERSITY                      | Txns:  501  | Amount:    1,918,164.44\n"
    "15. CANCER TRIALS AUSTRALIA                | Txns:  514  | Amount:    1,863,037.38\n"
    "16. KELLY OUTSOURCING AND CONSULTING GR    | Txns:  766  | Amount:    1,782,934.54\n"
    "17. JANSSEN PHARMACEUTICA NV               | Txns:   22  | Amount:    1,703,008.71\n"
    "18. MACQUARIE UNIVERSITY                   | Txns:  182  | Amount:    1,694,663.44\n"
    "19. LINEAR CLINICAL RESEARCH LTD           | Txns:  289  | Amount:    1,653,398.24\n"
    "20. STOCKLAND TRUST MANAGEMENT LTD         | Txns:   27  | Amount:    1,589,708.42\n"
    "21. Janssen-CILAG Taiwan                   | Txns:    1  | Amount:    1,549,069.00\n"
    "22. MAIN BEACH HOTEL PTY LTD T/A SHERAT    | Txns:   11  | Amount:    1,373,633.74\n"
    "23. AESIR HEALTH PTY LTD.                  | Txns:   40  | Amount:    1,271,430.57\n"
    "24. SYDNEY LOCAL HEALTH DISTRICT           | Txns:  217  | Amount:    1,175,116.08\n"
    "25. INSERVIO PTY LTD                       | Txns:  126  | Amount:    1,129,629.16\n"
    "                                                          --------------------------\n"
    "                                             TOTAL        1,352,213,059.08"
)

bullet("Note: Several payees appear to be government bodies, J&J affiliates, universities, or logistics "
       "providers — suggesting master-data mismatches or misclassification. However, the core control "
       "objective failed: TPI-designated disbursements went to non-inventoried supplier names.")

h_section("RiskOutput Written  —  Check 6  (Row 432)")
risk_table([
    ("risk_title",             "Potential Risk"),
    ("risk_type",              "Payment to Unknown Supplier — Not in TPI Inventory"),
    ("risk_category",          "High Risk  (Score: 8 / 10)"),
    ("scope_area",             "TPI Due Diligence"),
    ("risk_description",       "Automated testing identified 25 suppliers that received TPI-coded payments "
                               "totalling 1,352,213,059.08 across 6,524 transactions whose names did not match any "
                               "entity in the approved TPI Inventory. This indicates payments were processed to "
                               "parties not onboarded as approved TPIs, potentially bypassing required due diligence, "
                               "risk assessment, contracting, and anti-bribery controls. Several payees appear to be "
                               "government bodies, universities/hospitals, logistics/CRO providers, and J&J affiliates, "
                               "suggesting master-data mismatches or misclassification."),
    ("risk_rationale",         "J&J HCBI TPI Policy and Due Diligence Standards require that all third-party "
                               "intermediaries be registered in the TPI Inventory and complete risk screening, "
                               "contracting, and approvals prior to engagement and payment. Paying non-inventoried "
                               "parties breaches the approved-supplier-only control and undermines the completeness "
                               "and accuracy of the TPI register."),
    ("risk_category_reasoning","Score 8 assigned under the High band (8-10). Payments totalling 1,352,213,059.08 "
                               "were made to 25 suppliers with no match in the approved TPI Inventory, directly "
                               "indicating engagement of unapproved intermediaries. This bypasses all HCBI due "
                               "diligence controls and represents a significant control failure supported by "
                               "explicit transactional evidence."),
    ("calculation_evidence",   "25 supplier(s) not in TPI Inventory received payments totalling 1,352,213,059.08\n"
                               "across 6,524 transactions.\n"
                               "(See row-level results above for full supplier breakdown.)"),
    ("source_details",         "- Source: Transactional Data Analysis — Check 6;\n"
                               "  Document: payments_dashboard_summary; Section: Supplier Inventory Validation\n\n"
                               "- Source: TPI Inventory;\n"
                               "  Document: TPI_Inventory_Final_Output.xlsx; Section: Approved TPI Entities"),
])
row_written("ROW WRITTEN TO OUTPUT  [RED — Potential Risk, Score 8.0]")
placeholder("[ SCREENSHOT — Excel output row 432 showing Check 6 finding ]")


# ══════════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════════
out = r"F:\EXL\AI_Auditor\docs\AUDIT_DEMO_SCENARIOS_v4.docx"
doc.save(out)
print(f"Saved: {out}")
