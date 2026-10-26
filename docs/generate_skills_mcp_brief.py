"""Generate a concise (<=3 page) executive .docx: Skills & MCP positioning for senior leadership."""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

OUT = Path(__file__).resolve().parent / "Skills_and_MCP_Executive_Brief.docx"

NAVY = RGBColor(0x1E, 0x3A, 0x5F)


def h(doc, text, size=13):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(size)
    r.font.color.rgb = NAVY
    p.space_before = Pt(6)
    p.space_after = Pt(2)
    return p


def bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.add_run(text)
    p.paragraph_format.space_after = Pt(2)
    return p


def main():
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    # Title
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r = t.add_run("Skills & MCP — Executive Brief")
    r.bold = True
    r.font.size = Pt(18)
    r.font.color.rgb = NAVY
    sub = doc.add_paragraph()
    rs = sub.add_run("FCPA / TPI Due-Diligence AI Auditor  •  CA&I")
    rs.italic = True
    rs.font.size = Pt(10)
    rs.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

    # 1. Summary
    h(doc, "Summary")
    bullet(doc, "Skills and MCP are engineering conveniences (reusability and dynamic tooling). "
                "They do NOT change the analytical quality of the risk assessment.")
    bullet(doc, "Output quality is driven by the reasoning model (GPT-5), prompt design, and "
                "retrieval (RAG) — all already in place and performing.")
    bullet(doc, "“Skills” are native to Google ADK and to Claude. They are not native to our "
                "LangChain / LangGraph stack, so we deliver the same capabilities via prompts + typed "
                "tools + graph orchestration — with identical output quality.")
    bullet(doc, "Plan: once the pipeline output is validated, the same capabilities will be consumed "
                "in another way — via tool-calling (and packaged for reuse) — as a maintainability step, "
                "not to improve answers.")
    bullet(doc, "MCP (dynamic tool calling) will be added later; it carries added cost and slight "
                "latency, so it is deferred until the benefit is justified.")

    # 2. Why quality is unaffected
    h(doc, "Why quality is unaffected")
    p = doc.add_paragraph()
    p.add_run(
        "A “Skill” is a packaging layer above the model — a reusable capsule of instruction + "
        "resources. Whether a capability lives inside a Skill or inside a prompt + tool on LangGraph, "
        "the model reasons over the same inputs and reaches the same conclusion. Skills change how code "
        "is organised and reused, not what the model decides. The strong output seen at “skill level” "
        "on ADK / Claude comes from the model, prompts and retrieval underneath — which we reproduce "
        "exactly on GPT-5 + LangGraph."
    )

    # 3. ADK vs LangGraph table
    h(doc, "How we achieve the same thing (ADK vs LangGraph)")
    rows = [
        ("Aspect", "Google ADK (skill-native)", "LangGraph — our stack"),
        ("Reusable capability", "Skill (native primitive)", "Typed tool / graph node"),
        ("Instructions", "Inside the skill spec", "Inside the prompt"),
        ("Orchestration", "Runtime auto-orchestrates skills", "Explicit supervisor graph (deterministic)"),
        ("Reasoning model", "Model-agnostic", "GPT-5 (strong compliance reasoning)"),
        ("Dynamic tooling", "Toolsets / MCP", "Static typed tools today; MCP later"),
        ("Quality driver", "Model + prompts + retrieval", "Same — model + prompts + retrieval"),
        ("Net output quality", "—", "Identical (skills only affect reuse/packaging)"),
    ]
    table = doc.add_table(rows=len(rows), cols=3)
    table.style = "Light Grid Accent 1"
    for i, (a, b, c) in enumerate(rows):
        cells = table.rows[i].cells
        cells[0].text, cells[1].text, cells[2].text = a, b, c
        if i == 0:
            for cell in cells:
                for par in cell.paragraphs:
                    for run in par.runs:
                        run.bold = True
                        run.font.size = Pt(9.5)
        else:
            for cell in cells:
                for par in cell.paragraphs:
                    for run in par.runs:
                        run.font.size = Pt(9.5)

    p = doc.add_paragraph()
    rr = p.add_run("Only the model, prompts and retrieval rows touch quality — and those are equivalent "
                   "(GPT-5 is a strong reasoning choice). Every other difference is packaging and "
                   "orchestration, not correctness.")
    rr.italic = True
    rr.font.size = Pt(9.5)

    # 4. MCP
    h(doc, "MCP — deferred, with reasons")
    bullet(doc, "Purpose: dynamic tool discovery / binding at runtime (useful when the tool set changes).")
    bullet(doc, "Cost: additional runtime / infrastructure — a real cost factor to justify.")
    bullet(doc, "Latency: each dynamic tool call adds a little latency (handshake + round-trip).")
    bullet(doc, "Decision: deferred. Our static, typed tools cover today's need at lower cost and "
                "latency; MCP will be added later when dynamic tooling clearly pays for itself.")

    # 5. Roadmap / bottom line
    h(doc, "Roadmap & bottom line")
    bullet(doc, "1) Validate the output first.")
    bullet(doc, "2) Then consume the validated capabilities via tool-calling (package for reuse).")
    bullet(doc, "3) Add MCP for dynamic tooling when the cost / latency trade-off is justified.")
    p = doc.add_paragraph()
    rb = p.add_run("No output quality is lost today. Skills and MCP are additive, later — reusability "
                   "and dynamic tooling — not accuracy levers.")
    rb.bold = True
    rb.font.size = Pt(10.5)

    doc.save(OUT)
    print(f"Saved: {OUT}")


if __name__ == "__main__":
    main()
