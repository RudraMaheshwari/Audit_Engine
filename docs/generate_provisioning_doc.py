"""
Generates AI_Auditor_Provisioning_Requirements.docx
Run: python docs/generate_provisioning_doc.py
"""
from docx import Document
from docx.shared import Pt, RGBColor, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

doc = Document()

for section in doc.sections:
    section.top_margin    = Cm(1.5)
    section.bottom_margin = Cm(1.5)
    section.left_margin   = Cm(1.8)
    section.right_margin  = Cm(1.8)
    section.page_width    = Cm(33.87)   # A3 landscape
    section.page_height   = Cm(23.99)
    section.orientation   = 1           # landscape

# ── Colours ───────────────────────────────────────────────────────────────────
DARK_BLUE  = RGBColor(0x1F, 0x49, 0x7D)
RED_RGB    = "C00000"
NAVY_RGB   = "1F3764"
TEAL_RGB   = "1F5C4A"
HEADER_RGB = "D6DCE4"
EXTRA_RGB  = "FFF2CC"
WHITE_RGB  = "FFFFFF"
ALT_RGB    = "F2F2F2"

GREEN  = RGBColor(0x37, 0x86, 0x10)
ORANGE = RGBColor(0xC5, 0x5A, 0x11)
RED_C  = RGBColor(0xC0, 0x00, 0x00)
BLACK  = RGBColor(0x00, 0x00, 0x00)
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
GOLD   = RGBColor(0x7F, 0x60, 0x00)


def shd(cell, hex_color):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    s    = OxmlElement("w:shd")
    s.set(qn("w:val"),   "clear")
    s.set(qn("w:color"), "auto")
    s.set(qn("w:fill"),  hex_color)
    tcPr.append(s)


def borders(cell, color="BFBFBF"):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    b    = OxmlElement("w:tcBorders")
    for side in ("top","left","bottom","right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"),   "single")
        el.set(qn("w:sz"),    "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        b.append(el)
    tcPr.append(b)


def set_col_width(cell, width_cm):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcW  = OxmlElement("w:tcW")
    twips = int(Cm(width_cm) / 914.4 * 1440)
    tcW.set(qn("w:w"),    str(twips))
    tcW.set(qn("w:type"), "dxa")
    tcPr.append(tcW)


def set_vert_text(cell, text, bg_hex, font_color=WHITE):
    """Rotated text in merged group cell."""
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    # background
    s = OxmlElement("w:shd")
    s.set(qn("w:val"), "clear"); s.set(qn("w:color"), "auto"); s.set(qn("w:fill"), bg_hex)
    tcPr.append(s)
    # borders
    b = OxmlElement("w:tcBorders")
    for side in ("top","left","bottom","right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single"); el.set(qn("w:sz"), "6")
        el.set(qn("w:space"), "0"); el.set(qn("w:color"), "FFFFFF")
        b.append(el)
    tcPr.append(b)
    # vertical text direction
    td = OxmlElement("w:textDirection")
    td.set(qn("w:val"), "btLr")
    tcPr.append(td)
    # center alignment
    va = OxmlElement("w:vAlign")
    va.set(qn("w:val"), "center")
    tcPr.append(va)
    p   = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(9)
    run.font.color.rgb = font_color


def cell_text(cell, text, font_size=8, bold=False, color=BLACK,
              align=WD_ALIGN_PARAGRAPH.LEFT, bg_hex=None, italic=False):
    if bg_hex:
        shd(cell, bg_hex)
    borders(cell)
    p   = cell.paragraphs[0]
    p.alignment = align
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after  = Pt(1)
    run = p.add_run(text)
    run.font.size   = Pt(font_size)
    run.font.bold   = bold
    run.font.italic = italic
    run.font.color.rgb = color


# ══════════════════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════════════════
t = doc.add_paragraph()
t.alignment = WD_ALIGN_PARAGRAPH.CENTER
t.paragraph_format.space_after = Pt(4)
r = t.add_run("AI Auditor — Provisioning Requirements")
r.bold = True; r.font.size = Pt(15); r.font.color.rgb = DARK_BLUE

note = doc.add_paragraph()
note.alignment = WD_ALIGN_PARAGRAPH.CENTER
note.paragraph_format.space_after = Pt(6)
rn = note.add_run(
    "★  Items marked require separate provisioning sign-off."
)
rn.italic = True; rn.font.size = Pt(9); rn.font.color.rgb = GOLD

# ══════════════════════════════════════════════════════════════════════════════
# TABLE
# Cols: Group | Requirement | Usage | Tool | Availability | Access
# Widths (cm): 1.2 | 3.8 | 5.5 | 7.0 | 3.5 | 2.5  = 23.5cm
# ══════════════════════════════════════════════════════════════════════════════
COL_WIDTHS = [1.2, 3.8, 5.5, 7.0, 3.5, 2.5]

# rows = group_label | req | usage | tool | availability | access | extra | group_color
ROWS = [
    # ── DATA PREPARATION ──────────────────────────────────────────────────────
    ("Data Preparation", "Document Reading,\nValidation & OCR",
     "Reading policy PDFs, supporting docs and scanned image files (OCR extraction)",
     "Python — pypdf2, python-docx, openpyxl\nTesseract OCR (scanned image PDFs)",
     "Available with Databricks\n+ Tesseract install required",
     "In Progress", False, RED_RGB),

    ("Data Preparation", "★  Vector Search Indexes",
     "Semantic RAG retrieval across global policy, local policy and supporting evidence documents\n(3 separate indexes queried per question)",
     "Databricks Vector Search — Direct Access indexes\n(hcbi_index | policy_index | supporting_docs_index)",
     "Available with Databricks",
     "Completed", True, RED_RGB),

    ("Data Preparation", "Database",
     "Questionnaire input tables, output Delta tables, scope embeddings, inventory tables and payment/revenue tables",
     "Databricks Unity Catalog / Delta Tables\n(Catalog: cat_cbt_gaa_brz_dev)",
     "Available with Databricks",
     "Completed", False, RED_RGB),

    ("Data Preparation", "File Storage (Volumes)",
     "Store policy documents, supporting evidence files, inventory file and GL account map",
     "Databricks Volumes\n(/Volumes/.../global_policies | local_policies | supporting_documents)",
     "Available with Databricks",
     "Completed", False, RED_RGB),

    # ── MODEL DEVELOPMENT ─────────────────────────────────────────────────────
    ("Model Development", "Large Language Models",
     "6-agent audit pipeline: policy readers, evidence reader, gap detector,\ntransactional validator, risk decision maker",
     "Azure OpenAI — GPT-5\n(JNJ Internal Gateway: genaiapimna.jnj.com)",
     "Available via\nJNJ Internal Gateway",
     "Completed", False, NAVY_RGB),

    ("Model Development", "Embeddings",
     "Document chunking and semantic vector indexing for all 3 RAG indexes\n(global policy, local policy, supporting docs — ~50 chunks per document)",
     "Azure OpenAI — text-embedding-3-small\n(JNJ Internal Gateway: genaiapimna.jnj.com)",
     "Available via\nJNJ Internal Gateway",
     "Completed", False, NAVY_RGB),

    ("Model Development", "★  SQL Warehouse",
     "NL-to-SQL transactional queries against payment, credit memo and revenue Delta tables\n(live queries per question flagged with financial figures)",
     "Databricks SQL Warehouse  ★\n(payments_dashboard_summary | revenue_dashboard_credit_memo | revenue_dashboard_summary)",
     "Available with Databricks",
     "Completed", True, NAVY_RGB),

    ("Model Development", "Orchestration & Secrets",
     "Multi-agent parallel pipeline (up to 5 questions concurrently via asyncio)\nSecrets management for API keys and Databricks tokens",
     "Python asyncio (parallel orchestration)\nAzure Key Vault / Databricks Secrets Scope",
     "Available / Required",
     "Required", False, NAVY_RGB),

    ("Model Development", "Source Code & Version Tracking",
     "Source code version control, branching and merging for pipeline and frontend",
     "Bitbucket",
     "Required",
     "In Progress", False, NAVY_RGB),

    # ── DEPLOYMENT ────────────────────────────────────────────────────────────
    ("Deployment", "Deployment Environments / CI/CD",
     "Azure Cloud environments for DEV, UAT and PROD pipeline deployments",
     "Azure DEV, UAT, PROD\nJenkins for CI/CD",
     "Required",
     "Required", False, TEAL_RGB),

    ("Deployment", "Docker & Container Scanning",
     "Containerise the audit pipeline application; scan images for vulnerabilities",
     "Jrog (container registry)\nFortify / CheckMarx (code scanning)",
     "Required",
     "Required", False, TEAL_RGB),

    ("Deployment", "Application Hosting & Load Balancing",
     "Host audit pipeline backend (served via FastAPI + Uvicorn) and frontend dashboard;\nmanage load across concurrent audit runs",
     "Load Balancer Controller\nFastAPI + Uvicorn (backend entry point — replaces python main.py)\nStreamlit (frontend dashboard)",
     "Required",
     "Required", False, TEAL_RGB),

    ("Deployment", "Infrastructure as Code & Monitoring",
     "Monitor pipeline runs, API response times, audit job tracking and infrastructure metrics",
     "Application Insights, Azure Monitor\nDatabricks Job Monitoring, Langfuse",
     "Required",
     "Required", False, TEAL_RGB),
]

# Group spans
GROUP_SPANS = {
    "Data Preparation":  (0, 3,  RED_RGB),
    "Model Development": (4, 8,  NAVY_RGB),
    "Deployment":        (9, 12, TEAL_RGB),
}

TOTAL_ROWS = len(ROWS)
# Table: 1 header + data rows
tbl = doc.add_table(rows=1 + TOTAL_ROWS, cols=6)
tbl.style = "Table Grid"

# ── Header ────────────────────────────────────────────────────────────────────
HDR_LABELS = ["", "Requirement", "Usage", "Tool", "Availability", "Access"]
for ci, (label, w) in enumerate(zip(HDR_LABELS, COL_WIDTHS)):
    c = tbl.rows[0].cells[ci]
    set_col_width(c, w)
    shd(c, HEADER_RGB)
    borders(c, "FFFFFF")
    p   = c.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(label)
    run.bold = True; run.font.size = Pt(9); run.font.color.rgb = DARK_BLUE

# ── Data rows ─────────────────────────────────────────────────────────────────
for i, (grp, req, usage, tool, avail, access, extra, grp_color) in enumerate(ROWS):
    row  = tbl.rows[i + 1]
    bg   = EXTRA_RGB if extra else (ALT_RGB if i % 2 == 0 else WHITE_RGB)

    # Col 0 — group (filled later via merge)
    set_col_width(row.cells[0], COL_WIDTHS[0])
    shd(row.cells[0], grp_color)
    borders(row.cells[0], "FFFFFF")

    # Col 1 — Requirement
    set_col_width(row.cells[1], COL_WIDTHS[1])
    req_color = GOLD if extra else DARK_BLUE
    cell_text(row.cells[1], req, font_size=8.5, bold=True, color=req_color, bg_hex=bg)

    # Col 2 — Usage
    set_col_width(row.cells[2], COL_WIDTHS[2])
    cell_text(row.cells[2], usage, font_size=8, bg_hex=bg)

    # Col 3 — Tool
    set_col_width(row.cells[3], COL_WIDTHS[3])
    cell_text(row.cells[3], tool, font_size=8, color=RGBColor(0x1F,0x49,0x7D), bg_hex=bg)

    # Col 4 — Availability
    set_col_width(row.cells[4], COL_WIDTHS[4])
    cell_text(row.cells[4], avail, font_size=8, bg_hex=bg, align=WD_ALIGN_PARAGRAPH.CENTER)

    # Col 5 — Access
    set_col_width(row.cells[5], COL_WIDTHS[5])
    a_color = GREEN if access == "Completed" else (ORANGE if "Progress" in access or "Process" in access else RED_C)
    cell_text(row.cells[5], access, font_size=8.5, bold=True, color=a_color,
              bg_hex=bg, align=WD_ALIGN_PARAGRAPH.CENTER)

# ── Merge group cells and add rotated label ───────────────────────────────────
for grp_label, (r_start, r_end, color) in GROUP_SPANS.items():
    top_cell = tbl.rows[r_start + 1].cells[0]
    bot_cell = tbl.rows[r_end   + 1].cells[0]
    merged   = top_cell.merge(bot_cell)
    set_vert_text(merged, grp_label, color)

# ── Footer ────────────────────────────────────────────────────────────────────
doc.add_paragraph()
foot = doc.add_paragraph()
foot.paragraph_format.space_before = Pt(4)
fr = foot.add_run(
    "★ Items marked require separate provisioning sign-off — Databricks Vector Search and Databricks SQL Warehouse.  "
    "All other services are already approved and available within the existing infrastructure."
)
fr.italic = True; fr.font.size = Pt(8); fr.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

# ── Save ──────────────────────────────────────────────────────────────────────
out = r"F:\EXL\AI_Auditor\docs\AI_Auditor_Provisioning_Requirements_v3.docx"
doc.save(out)
print(f"Saved: {out}")
