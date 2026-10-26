"""
AI Auditor — 4-page Professional Client Demo Deck
Slide 2: As-Is vs To-Be visual flow (inspired by QNXT style)
Run: python docs/generate_demo_presentation.py
"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.oxml import parse_xml

W = Inches(13.33)
H = Inches(7.5)
prs = Presentation()
prs.slide_width  = W
prs.slide_height = H

# ── Palette ──────────────────────────────────────────────────────────────────
ORANGE    = RGBColor(0xF0, 0x5A, 0x28)
ORANGE_D  = RGBColor(0xC4, 0x40, 0x18)
PEACH_HDR = RGBColor(0xFD, 0xE8, 0xDC)
PEACH_ROW = RGBColor(0xFD, 0xF3, 0xEF)
WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
RULE_CLR  = RGBColor(0xF5, 0xCA, 0xB8)
TEXT      = RGBColor(0x1A, 0x1A, 0x2E)
BODY      = RGBColor(0x4A, 0x55, 0x68)
CAPTION   = RGBColor(0x71, 0x80, 0x96)
FTR_BG    = RGBColor(0xF7, 0xF7, 0xF7)
FTR_TXT   = RGBColor(0xA0, 0xAE, 0xBE)
DARK_PANEL= RGBColor(0x8B, 0x2C, 0x08)   # dark burnt orange for right panel

X0 = Inches(0.22)
CW = W - Inches(0.36)
CT = Inches(1.28)
CB = H - Inches(0.24)

# ── Helpers ───────────────────────────────────────────────────────────────────
def blank():
    return prs.slides.add_slide(prs.slide_layouts[6])

def _fill(sp, rgb):
    spPr = sp.find(qn("p:spPr"))
    if spPr is None:
        spPr = parse_xml(
            '<p:spPr xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
        sp.append(spPr)
    for tag in ("a:solidFill","a:noFill","a:gradFill","a:pattFill"):
        for o in spPr.findall(qn(tag)): spPr.remove(o)
    spPr.append(parse_xml(
        f'<a:solidFill xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<a:srgbClr val="{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"/></a:solidFill>'))

def rect(slide, l, t, w, h, rgb):
    sh = slide.shapes.add_shape(1, l, t, w, h)
    sh.fill.solid(); sh.fill.fore_color.rgb = rgb
    sh.line.fill.background()
    return sh

def oval(slide, l, t, d, fill, text, size=9, bold=True, tc=WHITE):
    sh = slide.shapes.add_shape(9, l, t, d, d)
    sh.fill.solid(); sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    tf = sh.text_frame
    tf.word_wrap = False
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = text
    r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = tc
    return sh

def arrow(slide, l, t, w, h):
    sh = slide.shapes.add_shape(13, l, t, w, h)  # RIGHT_ARROW
    sh.fill.solid(); sh.fill.fore_color.rgb = ORANGE
    sh.line.fill.background()
    return sh

def tb(slide, l, t, w, h, fill, lines, sizes,
       bolds=None, colors=None,
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE, wrap=True):
    sh = slide.shapes.add_textbox(l, t, w, h)
    _fill(sh._element, fill)
    tf = sh.text_frame
    tf.word_wrap = wrap
    tf.auto_size = None
    tf.vertical_anchor = anchor
    if not isinstance(lines, list): lines = [lines]
    n = len(lines)
    _s = sizes  if isinstance(sizes, list) else [sizes]*n
    _b = bolds  if isinstance(bolds, list) else [bolds]*n
    _c = colors if isinstance(colors, list) else [colors]*n
    for i, txt in enumerate(lines):
        p = tf.paragraphs[i] if i == 0 else tf.add_paragraph()
        p.alignment = align
        r = p.add_run(); r.text = str(txt)
        r.font.size = Pt(_s[i])
        r.font.bold  = bool(_b[i]) if _b[i] is not None else False
        r.font.color.rgb = _c[i] if _c[i] else TEXT
    return sh

def slide_header(slide, title, subtitle=None):
    rect(slide, 0, 0, W, Inches(1.22), ORANGE)
    rect(slide, 0, 0, Inches(0.10), H, ORANGE_D)
    tb(slide, Inches(0.22), Inches(0.08), W - Inches(0.32), Inches(0.66),
       ORANGE, [title], [26], [True], [WHITE], align=PP_ALIGN.LEFT)
    if subtitle:
        tb(slide, Inches(0.22), Inches(0.74), W - Inches(0.32), Inches(0.38),
           ORANGE, [subtitle], [9.5], [False],
           [RGBColor(0xFF,0xD5,0xC2)], align=PP_ALIGN.LEFT)
    rect(slide, Inches(0.10), Inches(1.22), W - Inches(0.10), Inches(0.06), RULE_CLR)

def slide_footer(slide):
    rect(slide, 0, H - Inches(0.24), W, Inches(0.24), FTR_BG)
    tb(slide, X0, H - Inches(0.24), CW, Inches(0.24), FTR_BG,
       ["CONFIDENTIAL   ·   EXL Service   ·   AI Auditor   ·   2025"],
       [7.5], [False], [FTR_TXT], align=PP_ALIGN.CENTER)

def sub_bar(slide, l, t, w, text, size=8.5):
    tb(slide, l, t, w, Inches(0.26), PEACH_HDR,
       [f"  {text}"], [size], [True], [ORANGE_D],
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
    return t + Inches(0.26)

def agent_pill(slide, l, t, w, h, text, size=8):
    tb(slide, l, t, w, h, PEACH_HDR,
       [text], [size], [True], [ORANGE_D],
       align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

def col_pill(slide, l, t, w, h, text, size=7):
    tb(slide, l, t, w, h, ORANGE,
       [text], [size], [True], [WHITE],
       align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

def card(i): return PEACH_ROW if i % 2 == 0 else WHITE


# ═══════════════════════════════════════════════════════════════════════════
#  SLIDE 1 — Title & Executive Overview
# ═══════════════════════════════════════════════════════════════════════════
s1 = blank()
rect(s1, 0, 0, W, H, WHITE)
slide_header(s1,
    "AI AUDITOR  —  MULTI-AGENT PLATFORM",
    "Johnson & Johnson   ·   TPI Due Diligence Compliance Automation   ·   Powered by LangGraph + Azure OpenAI GPT-5")

SB_H   = Inches(0.60)
SB_TOP = CB - SB_H
rect(s1, 0, SB_TOP - Inches(0.03), W, Inches(0.03), RULE_CLR)
stats = [("8","AI Agents"),("48","FCPA Scopes"),("4","Vector Indexes"),
         ("3","Policy Layers"),("3,072","Embedding Dims"),("GPT-5","LLM Engine")]
sw = W / len(stats)
for i, (num, lbl) in enumerate(stats):
    tb(s1, i*sw, SB_TOP, sw, SB_H, ORANGE if i%2==0 else ORANGE_D,
       [num, lbl], [20, 7.5], [True, False], [WHITE, WHITE], align=PP_ALIGN.CENTER)

COL_H = SB_TOP - CT - Inches(0.04)
CG    = Inches(0.10)
CW3   = (CW - 2*CG) / 3
CX    = [X0, X0+CW3+CG, X0+2*(CW3+CG)]

sub_bar(s1, CX[0], CT, CW3, "  What Is It?")
col1 = ["An AI-native audit engine that automates",
        "the manual TPI Due Diligence review process.",
        " ","Auditors upload questionnaire Q&A responses.",
        "Eight specialised AI agents analyse every",
        "question against three policy layers and the",
        "auditor's own supporting evidence documents,",
        "then produce a risk-classified, evidence-",
        "backed Excel report — automatically.",
        " ",
        "Platform:         Databricks on Azure",
        "LLM:                Azure OpenAI GPT-5",
        "Orchestration:  LangGraph supervisor",
        "Retrieval:          Databricks Vector Search",
        "Output:             Excel (colour-coded)"]
tb(s1, CX[0], CT+Inches(0.26), CW3, COL_H-Inches(0.26), PEACH_ROW,
   col1, [9]*15, [False]*10+[True]*5, [TEXT]*10+[BODY]*5,
   align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP)

sub_bar(s1, CX[1], CT, CW3, "  Challenges Addressed")
challenges = [
    ("Manual review bottleneck","Hours per question, inconsistent across auditors"),
    ("Policy coverage gaps","HCBI global vs. local TPI policy conflicts missed"),
    ("No evidence linkage","Findings not grounded in supporting documents"),
    ("Missing scope mapping","Questions not mapped to 48 FCPA audit scopes"),
    ("Transactional blind spot","No automated check on payments / revenue data"),
    ("No audit trail","Reasoning not captured; findings not reproducible"),
    ("Manual Excel reporting","Time-consuming, error-prone, no risk colour-coding"),
]
rh = (COL_H - Inches(0.26)) / len(challenges)
for i, (t2, d) in enumerate(challenges):
    tb(s1, CX[1], CT+Inches(0.26)+i*rh, CW3, rh, card(i),
       [t2, d], [8.5, 8], [True, False], [TEXT, BODY],
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

sub_bar(s1, CX[2], CT, CW3, "  Value Delivered")
values = [
    ("8 specialised AI agents","Analyse every question across parallel phases"),
    ("3 policy layers","HCBI + local TPI + FCPA scope guidance"),
    ("Hybrid retrieval","BM25 reranker fused with semantic vector search"),
    ("NL-to-SQL on Delta tables","Automated payments & revenue transactional checks"),
    ("48 FCPA scopes covered","Every scope area analysed in Phase 3"),
    ("Evidence-backed findings","Every risk linked to source documents by name"),
    ("Auto-generated Excel report","Colour-coded by risk category, incremental save"),
]
vh = (COL_H - Inches(0.26)) / len(values)
for i, (t2, d) in enumerate(values):
    tb(s1, CX[2], CT+Inches(0.26)+i*vh, CW3, vh, card(i),
       [t2, d], [8.5, 8], [True, False], [TEXT, BODY],
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

slide_footer(s1)


# ═══════════════════════════════════════════════════════════════════════════
#  SLIDE 2 — As-Is vs To-Be  (visual flow)
# ═══════════════════════════════════════════════════════════════════════════
s2 = blank()
rect(s2, 0, 0, W, H, WHITE)
slide_header(s2,
    "HOW IT WORKS  —  FROM MANUAL TO AI-DRIVEN AUDIT",
    "Transforming hours of manual policy review into automated, evidence-backed, consistent risk findings")

# ── Panel layout ─────────────────────────────────────────────────────────────
LP_W = Inches(10.04)   # left panel   X0=0.22  →  10.26
RP_X = Inches(10.36)   # right panel  10.36  →  13.11
RP_W = Inches(2.75)
SEP  = Inches(0.06)    # gap between as-is / to-be

ASIS_TOP = CT                          # 1.28
ASIS_H   = Inches(2.72)
ASIS_BOT = ASIS_TOP + ASIS_H          # 4.00
TOBE_TOP = ASIS_BOT + SEP             # 4.06
TOBE_BOT = CB                          # 7.26
TOBE_H   = TOBE_BOT - TOBE_TOP        # 3.20

# ═════════════════
#  RIGHT PANEL (benefits)  — drawn first so it spans full height
# ═════════════════
rect(s2, RP_X, CT, RP_W, TOBE_BOT - CT, DARK_PANEL)

# Badge "3" + heading
oval(s2, RP_X + Inches(0.08), CT + Inches(0.06), Inches(0.30), WHITE, "3", 9, True, ORANGE)
tb(s2, RP_X + Inches(0.44), CT + Inches(0.04), RP_W - Inches(0.52), Inches(0.38),
   DARK_PANEL, ["Key Benefits", "and Impact"],
   [10.5, 10.5], [True, True], [WHITE, WHITE], align=PP_ALIGN.LEFT)
rect(s2, RP_X + Inches(0.08), CT + Inches(0.52), RP_W - Inches(0.16), Inches(0.03), ORANGE)

benefits = [
    ("80%+", "Time Saved",          "Audit questions processed\nin minutes, not hours"),
    ("100%", "Policy Coverage",     "HCBI + local TPI + FCPA\nscope checked every time"),
    ("8x",   "Evidence Linkage",    "Every finding grounded in\nactual source documents"),
    ("48",   "Scopes Analysed",     "Full transactional check\nacross all FCPA scope areas"),
]
bh = (TOBE_BOT - CT - Inches(0.58)) / len(benefits)
for i, (big, title, detail) in enumerate(benefits):
    by = CT + Inches(0.60) + i*bh
    rect(s2, RP_X + Inches(0.08), by, RP_W - Inches(0.16), bh - Inches(0.04),
         RGBColor(0x9E, 0x32, 0x0A))
    tb(s2, RP_X + Inches(0.12), by + Inches(0.04), RP_W - Inches(0.24), Inches(0.34),
       RGBColor(0x9E, 0x32, 0x0A), [big], [22], [True], [WHITE], align=PP_ALIGN.LEFT)
    tb(s2, RP_X + Inches(0.12), by + Inches(0.34), RP_W - Inches(0.24), Inches(0.24),
       RGBColor(0x9E, 0x32, 0x0A), [title], [9], [True], [RULE_CLR],
       align=PP_ALIGN.LEFT)
    tb(s2, RP_X + Inches(0.12), by + Inches(0.56), RP_W - Inches(0.24), bh - Inches(0.66),
       RGBColor(0x9E, 0x32, 0x0A), [detail], [7.5], [False],
       [RGBColor(0xF5, 0xCA, 0xB8)], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP)

# ═════════════════
#  AS-IS SECTION
# ═════════════════
# Header bar
rect(s2, X0, ASIS_TOP, LP_W, Inches(0.30), PEACH_HDR)
oval(s2, X0 + Inches(0.04), ASIS_TOP + Inches(0.01),
     Inches(0.28), ORANGE, "1", 9, True, WHITE)
tb(s2, X0 + Inches(0.38), ASIS_TOP, Inches(5.0), Inches(0.30),
   PEACH_HDR, ["As-Is  —  Current Manual Audit Process"],
   [9.5], [True], [ORANGE_D], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
tb(s2, X0 + LP_W - Inches(3.2), ASIS_TOP, Inches(3.2), Inches(0.30),
   PEACH_HDR, ["Manual   ·   Hours per Question   ·   Inconsistent"],
   [8], [False], [BODY], align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)

# Timeline
N = 6
step_w  = LP_W / N
y_line  = ASIS_TOP + Inches(0.30) + Inches(0.68)   # 1.28+0.30+0.68 = 2.26
cd      = Inches(0.36)   # circle diameter
y_circle= y_line - cd / 2

# Draw line (behind circles)
rect(s2, X0, y_line - Inches(0.025), LP_W, Inches(0.05), RULE_CLR)
# Orange filled portion
rect(s2, X0, y_line - Inches(0.025), LP_W * 0.95, Inches(0.05), ORANGE)

asis_steps = [
    ("i",   "Receive Q&A",
     "Questionnaire\nquestion + auditor\nresponse received"),
    ("ii",  "HCBI Lookup",
     "Manually search\n115-page global\npolicy PDF"),
    ("iii", "Local Policy",
     "Review TPEC,\nRed Flag & SOP\npolicy documents"),
    ("iv",  "Evidence Read",
     "Manually read\nthrough auditor\nsupporting docs"),
    ("v",   "Risk Judgement",
     "Subjective risk\nassessment based\non experience"),
    ("vi",  "Excel Entry",
     "Manually populate\nfindings & rationale\nin Excel report"),
]

# Step name area (above circles)
name_y = ASIS_TOP + Inches(0.32)
name_h = y_circle - name_y - Inches(0.02)

# Card area (below circles)
card_y = y_circle + cd + Inches(0.04)
card_h = ASIS_BOT - card_y

for k, (roman, name, detail) in enumerate(asis_steps):
    cx_center = X0 + k*step_w + step_w/2
    # Step name above line
    tb(s2, X0 + k*step_w, name_y, step_w - Inches(0.03), name_h,
       card(k), [name], [8], [True], [TEXT],
       align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    # Circle on line
    oval(s2, cx_center - cd/2, y_circle, cd, ORANGE, roman, 8, True, WHITE)
    # Detail card below
    tb(s2, X0 + k*step_w, card_y, step_w - Inches(0.03), card_h,
       card(k), [detail], [7.5], [False], [BODY],
       align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# Separator line between sections
rect(s2, X0, ASIS_BOT + Inches(0.01), LP_W, Inches(0.03), RULE_CLR)

# ═════════════════
#  TO-BE SECTION
# ═════════════════
# Header bar  (solid orange — elevated importance)
rect(s2, X0, TOBE_TOP, LP_W, Inches(0.32), ORANGE)
oval(s2, X0 + Inches(0.04), TOBE_TOP + Inches(0.02),
     Inches(0.28), WHITE, "2", 9, True, ORANGE)
tb(s2, X0 + Inches(0.38), TOBE_TOP, Inches(5.5), Inches(0.32),
   ORANGE, ["To-Be  —  AI Multi-Agent System  (8 Agents  ·  Automated  ·  Evidence-Backed)"],
   [9.5], [True], [WHITE], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

# Flow boxes + arrows
# Phase 0 | -> | Phase A | -> | Phase B | -> | Output
TOBE_FLOW_TOP = TOBE_TOP + Inches(0.36)
TOBE_FLOW_H   = TOBE_BOT - TOBE_FLOW_TOP

ARR_W = Inches(0.42)
ARR_H = Inches(0.36)

# Box widths (must sum to LP_W - 3*ARR_W)
box_ws = [Inches(1.72), Inches(2.96), Inches(3.60), Inches(1.02)]
# 1.72+2.96+3.60+1.02 = 9.30 | 3*0.42=1.26 | total=10.56 -- too wide
# LP_W=10.04, arrows=1.26, box_total=8.78
box_ws = [Inches(1.66), Inches(2.86), Inches(3.26), Inches(1.00)]
# 1.66+2.86+3.26+1.00 = 8.78 | 8.78+1.26=10.04 = LP_W ✓

tobe_boxes = [
    ("PHASE 0",
     "Scope Identifier",
     ["Reads question +",
      "response + follow-up",
      "",
      "Maps to 1 of 48",
      "FCPA audit scope areas",
      "",
      "3-tier confidence routing:",
      "High / Medium / Low conf.",
      "",
      "Retries once if no",
      "scope returned"],
     "1 Agent"),
    ("PHASE A",
     "Parallel Retrieval  (asyncio.gather)",
     ["HCBI Policy Agent",
      "  hcbi_policy_index  top-k=5",
      "  MQR n=2 phrasings",
      "",
      "Policy Knowledge Agent",
      "  fcpa_scope_policy + DD policy",
      "  scope-filtered retrieval",
      "",
      "Supporting Docs Agent",
      "  BM25 + RRF hybrid reranker",
      "  per-auditor folder filter"],
     "3 Agents  ·  run simultaneously"),
    ("PHASE B",
     "Sequential Analysis",
     ["Conflict Detector",
      "  cross-row similarity > 0.90",
      "",
      "Risk Evaluation Agent  (GPT-5)",
      "  compliance gaps + risk level",
      "  sets transactional_intent flag",
      "",
      "Risk Classifier Agent  (GPT-5)",
      "  9-field structured output",
      "",
      "Risk Writer Agent  (GPT-5)",
      "  final fields + Excel save"],
     "4 Agents  ·  run in sequence"),
    ("OUTPUT",
     "FCPA Audit Excel",
     ["Risk Title",
      "Risk Type & Category",
      "Risk Rationale",
      "Scope Area",
      "Evidence Reference",
      "Source Documents",
      "Transaction Samples",
      "",
      "Colour-coded rows:",
      "High=red  Med=yellow"],
     "Auto-saved per question"),
]

x_cur = X0
for bi, (phase, subtitle, lines, footer) in enumerate(tobe_boxes):
    bw = box_ws[bi]
    box_w_inner = bw - Inches(0.04)

    # Box outline (light peach bg)
    rect(s2, x_cur, TOBE_FLOW_TOP, box_w_inner, TOBE_FLOW_H, card(bi))

    # Phase label (orange header)
    rect(s2, x_cur, TOBE_FLOW_TOP, box_w_inner, Inches(0.28), ORANGE)
    tb(s2, x_cur, TOBE_FLOW_TOP, box_w_inner, Inches(0.28),
       ORANGE, [phase], [9], [True], [WHITE], align=PP_ALIGN.CENTER)

    # Subtitle
    tb(s2, x_cur, TOBE_FLOW_TOP + Inches(0.28), box_w_inner, Inches(0.24),
       PEACH_HDR, [subtitle], [7.5], [True], [ORANGE_D],
       align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

    # Content lines
    content_top = TOBE_FLOW_TOP + Inches(0.52)
    content_h   = TOBE_FLOW_H - Inches(0.52) - Inches(0.26)
    tb(s2, x_cur + Inches(0.07), content_top,
       box_w_inner - Inches(0.10), content_h,
       card(bi), lines, [7.5]*len(lines),
       [False]*len(lines), [BODY]*len(lines),
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP)

    # Footer bar
    rect(s2, x_cur, TOBE_BOT - Inches(0.26), box_w_inner, Inches(0.26), PEACH_HDR)
    tb(s2, x_cur, TOBE_BOT - Inches(0.26), box_w_inner, Inches(0.26),
       PEACH_HDR, [footer], [7], [True], [ORANGE_D],
       align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

    x_cur += bw

    # Arrow (if not last box)
    if bi < 3:
        arr_y = TOBE_FLOW_TOP + TOBE_FLOW_H / 2 - ARR_H / 2
        arrow(s2, x_cur, arr_y, ARR_W - Inches(0.04), ARR_H)
        x_cur += ARR_W

# Phase 3 footnote inside To-Be area
tb(s2, X0, TOBE_BOT - Inches(0.50), LP_W * 0.55, Inches(0.22),
   WHITE,
   ["  + Phase 3: TransactionalDataAnalysisAgent — AI-driven SQL on all 48 FCPA scopes after Q&A rows"],
   [7], [False], [CAPTION], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

slide_footer(s2)


# ═══════════════════════════════════════════════════════════════════════════
#  SLIDE 3 — Agent Deep-Dive & Phase 3
# ═══════════════════════════════════════════════════════════════════════════
s3 = blank()
rect(s3, 0, 0, W, H, WHITE)
slide_header(s3, "AGENT DEEP-DIVE  —  ALL 8 AGENTS  &  PHASE 3 TRANSACTIONAL ANALYSIS")

NOTE_H  = Inches(0.26)
TABLE_H = CB - CT - NOTE_H - Inches(0.06)
LW = Inches(7.56)
RX = X0 + LW + Inches(0.12)
RW = W - RX - Inches(0.14)

COL_X = [X0, X0+Inches(1.90), X0+Inches(2.50), X0+Inches(4.96)]
COL_W = [Inches(1.84), Inches(0.54), Inches(2.40), Inches(2.54)]
HDR_H = Inches(0.28)
for hdr, cw, cx in zip(["AGENT","PH.","ROLE","KEY MECHANISM"], COL_W, COL_X):
    tb(s3, cx, CT, cw-Inches(0.03), HDR_H, PEACH_HDR,
       [hdr], [8], [True], [ORANGE_D], align=PP_ALIGN.CENTER)

rows = [
    ("Scope Identifier",   "0","Maps question to 48 FCPA scope areas",   "LLM 4-line output  ·  3-tier confidence routing  ·  retry once"),
    ("HCBI Policy Agent",  "A","Retrieves HCBI global policy context",    "MQR n=2  ·  hcbi_policy_index  ·  top-k=5  ·  async gather"),
    ("Policy Knowledge",   "A","Retrieves FCPA scope + DD policy",        "ScopePolicyTool (always)  ·  DDPolicyTool (DUE_DILIGENCE only)"),
    ("Supporting Docs",    "A","Retrieves auditor-specific evidence docs", "MQR n=3  ·  BM25+RRF reranker  ·  auditor_folder filter"),
    ("Risk Evaluation",    "B","Identifies compliance gaps + risk level",  "GPT-5 reasoning only  ·  sets transactional_intent flag"),
    ("Transactional Data", "B","NL-to-SQL on payments / revenue tables",  "CONDITIONAL  ·  Delta tables  ·  quantitative signal required"),
    ("Risk Classifier",    "B","9-field structured risk classification",   "Risk Type · Category · Evidence · Gap · GL Mismatch · Scope"),
    ("Risk Writer",        "B","Generates all final output fields",        "Risk Title/Desc/Rationale  ·  Scope Reasoning  ·  Excel save"),
]
rh = TABLE_H / len(rows)
for ri, (name, ph, role, mech) in enumerate(rows):
    ry = CT + HDR_H + ri*rh
    cf = card(ri)
    tb(s3, COL_X[0], ry, COL_W[0]-Inches(0.03), rh, cf,
       [name], [8.5], [True], [TEXT], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
    tb(s3, COL_X[1], ry, COL_W[1]-Inches(0.03), rh, cf,
       [ph], [12], [True], [ORANGE], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    tb(s3, COL_X[2], ry, COL_W[2]-Inches(0.03), rh, cf,
       [role], [8], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
    tb(s3, COL_X[3], ry, COL_W[3]-Inches(0.03), rh, cf,
       [mech], [7.5], [False], [CAPTION], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

tb(s3, X0, CB-NOTE_H, LW, NOTE_H, PEACH_ROW,
   ["  Guardrails G1-G10 on all agents  ·  Pydantic structured-output validation  ·  SQL: SELECT-only · try_cast · deterministic LIMIT"],
   [7.5], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

nxt_r = sub_bar(s3, RX, CT, RW, "PHASE 3  —  TRANSACTIONAL DATA ANALYSIS")
tb(s3, RX, nxt_r, RW, Inches(0.36), PEACH_ROW,
   ["AI-driven  ·  Runs ONCE after all question rows\nAll 48 FCPA scopes  ·  Concurrent thread pool (6 workers)"],
   [8], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
nxt_r += Inches(0.36)

steps = [
    ("LOAD",   "CPR guidance for all 48 scopes\nrisk_profile · red_flags · data_calculations\nsampling_rationale · expected_sample_size"),
    ("STEP 1", "LLM: Data Calculations (col I) to SQL\nUses FCPA intelligence if guidance thin\ntry_cast for STRING numerics  ·  LIMIT 25"),
    ("STEP 2", "Execute SQL against Delta tables\nScope-specific computed data result\nFalls back to shared summary if empty"),
    ("STEP 3", "LLM: ONE consolidated risk assessment\nRisk Type · Risk Category · Score\nDescription + Rationale (all concerns merged)"),
    ("STEP 4", "If risk detected: pull transaction samples\nSELECT *  LIMIT = Expected Sample Size + 5\nExact source table from query FROM clause"),
    ("OUTPUT", "~48 rows (one per scope area)\nNo Risk scopes emit row with no samples\nAppended to Excel after question rows"),
]
avail = CB - nxt_r - NOTE_H - Inches(0.06)
sh_h  = avail / len(steps)
for si, (lbl, det) in enumerate(steps):
    sy = nxt_r + si*sh_h
    agent_pill(s3, RX, sy, Inches(0.76), sh_h-Inches(0.03), lbl, 7.5)
    tb(s3, RX+Inches(0.78), sy, RW-Inches(0.78), sh_h-Inches(0.03),
       card(si), [det], [7.5], [False], [BODY],
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

slide_footer(s3)


# ═══════════════════════════════════════════════════════════════════════════
#  SLIDE 4 — Output · Governance · Observability · Tech Stack
# ═══════════════════════════════════════════════════════════════════════════
s4 = blank()
rect(s4, 0, 0, W, H, WHITE)
slide_header(s4, "OUTPUT  ·  GOVERNANCE  ·  OBSERVABILITY  ·  TECHNOLOGY STACK")

CG4 = Inches(0.10)
CW4 = (CW - 2*CG4) / 3
C1X, C2X, C3X = X0, X0+CW4+CG4, X0+2*(CW4+CG4)

y1 = sub_bar(s4, C1X, CT, CW4, "OUTPUT  —  FCPA_Audit_<AUDITOR_ID>_<ts>.xlsx")
agent_pill(s4, C1X, y1, CW4, Inches(0.22), "  Output A — Risk Detail", 7.5)
y1 += Inches(0.22)
out_a = ["Risk Title  ·  Risk Type","Risk Category  ·  Risk Category Reasoning",
         "Risk Rationale  ·  Risk Description","Scope Area  ·  Scope Area Reasoning",
         "Evidence Explanation  ·  Calculation Evidence",
         "Samples (transaction rows)  ·  Source Details",
         "Question  ·  Response  ·  Follow-up Q & R"]
oa_h = Inches(2.10) / len(out_a)
for i, itm in enumerate(out_a):
    tb(s4, C1X, y1+i*oa_h, CW4, oa_h, card(i),
       [f"  {itm}"], [8], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
y1 += Inches(2.10)
agent_pill(s4, C1X, y1, CW4, Inches(0.22), "  Output B — Scope Summary", 7.5)
y1 += Inches(0.22)
out_b = ["Count: Potential Risk  ·  Further Review  ·  No Risk",
         "Top compliance gaps  ·  Average category score",
         "Row colour: High=red · Med/Low=yellow · No Risk=none"]
ob_h = Inches(0.72) / len(out_b)
for i, itm in enumerate(out_b):
    tb(s4, C1X, y1+i*ob_h, CW4, ob_h, card(i),
       [f"  {itm}"], [8], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
y1 += Inches(0.72) + Inches(0.06)
y1 = sub_bar(s4, C1X, y1, CW4, "GOVERNANCE & GUARDRAILS")
gov = ["G1-G10 guardrails on all 8 agent system prompts",
       "G1-G6: hallucination · data invention · bias",
       "        FCPA scope · no legal advice · PII",
       "G7-G10: valid scope IDs · exact output format",
       "         confidence calibration",
       "Pydantic structured-output on every agent",
       "SQL: SELECT-only · try_cast · LIMIT override",
       "FastAPI: X-API-Key · 10 req/min · sanitization"]
rem = CB - y1
for i, itm in enumerate(gov):
    tb(s4, C1X, y1+i*(rem/len(gov)), CW4, rem/len(gov), card(i),
       [f"  {itm}"], [7.5], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

y2 = sub_bar(s4, C2X, CT, CW4, "OBSERVABILITY  —  Lakebase Postgres")
obs = [("Observability Store (OTEL)",
        "OpenTelemetry protocol emitted by every agent\nand the pipeline on every call.\nCaptures: traces · spans · metrics · logs\nBackend: Lakebase Postgres (Databricks)"),
       ("Feedback Table",
        "End-user feedback captured on the UI.\nRating + comments per finding.\nKeyed to: job_id · question_id · scope_id\nBackend: Lakebase Postgres"),
       ("Prior Audit Context",
        "Past audit summaries surfaced per question.\nPastAuditSummarizerAgent condenses prior\nfindings injected into Risk Evaluation\nagent context window.")]
ob_h2 = Inches(2.86) / len(obs)
for i, (title, det) in enumerate(obs):
    oy = y2 + i*ob_h2
    agent_pill(s4, C2X, oy, CW4, Inches(0.22), f"  {title}", 7.5)
    tb(s4, C2X, oy+Inches(0.22), CW4, ob_h2-Inches(0.22), card(i),
       [det], [7.5], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
y2 += Inches(2.86) + Inches(0.06)
y2 = sub_bar(s4, C2X, y2, CW4, "FASTAPI LAYER  (async)")
api = [("POST /audit","AuditRequest → background task → job_id (202)"),
       ("GET /status/{job_id}","Poll: pending / complete / failed"),
       ("GET /health","Liveness probe (200 if process is up)"),
       ("GET /ready","Vector Search + LLM connectivity check"),
       ("Auth","SSO on UI  ·  X-API-Key on backend"),
       ("Safety","Rate limit 10 req/min  ·  Input sanitization")]
ah = (CB - y2) / len(api)
for i, (ep, det) in enumerate(api):
    tb(s4, C2X, y2+i*ah, CW4, ah, card(i),
       [ep, det], [8, 7.5], [True, False], [TEXT, BODY],
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

y3 = sub_bar(s4, C3X, CT, CW4, "TECHNOLOGY STACK")
tech = [("AI / LLM",
         "Azure OpenAI GPT-5\n  All 8 agents · Phase 3 · MQR generation\nAzure OpenAI text-embedding-3-large\n  3072 dims · ingestion + all retrieval\nLangChain · LangGraph (supervisor + ReAct)"),
        ("Retrieval",
         "Databricks Vector Search  (4 indexes)\nBM25Reranker + RRF fusion  (rank-bm25)\nMultiQueryRetriever  (n=2 or 3 phrasings)\nContextAggregator: HCBI > Policy > Docs\nDelta Lake  (transactional data tables)"),
        ("Document Ingestion / OCR",
         "Azure Document Intelligence (prebuilt-read)\nTesseract OCR  (fallback)\npdfplumber · PyMuPDF  (PDF pages)\npython-pptx · python-docx · openpyxl\nBeautifulSoup · extract-msg"),
        ("Platform / Infrastructure",
         "Databricks Unity Catalog on Azure\nLakebase Postgres  (observability + feedback)\nOpenTelemetry (OTEL)  ·  FastAPI  ·  asyncio\nSQLAlchemy · Pydantic · pydantic-settings\nopenpyxl  (Excel output)")]
tgh = (CB - y3) / len(tech)
for i, (title, det) in enumerate(tech):
    ty = y3 + i*tgh
    agent_pill(s4, C3X, ty, CW4, Inches(0.22), f"  {title}", 7.5)
    tb(s4, C3X, ty+Inches(0.22), CW4, tgh-Inches(0.22), card(i),
       [det], [7.5], [False], [BODY], align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)

slide_footer(s4)

# ── Save ──────────────────────────────────────────────────────────────────────
out = "docs/AI_Auditor_Demo_v6.pptx"
prs.save(out)
print(f"Saved: {out}")
