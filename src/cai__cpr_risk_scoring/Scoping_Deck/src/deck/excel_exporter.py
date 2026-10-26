"""Generates a reference Excel workbook alongside the scoping deck.

Sheet 1 — one row per piece of generated content:
  Slide | Section | Content | Source Type | Source Detail | Criticality | Content Justification

Sheet 2 — one row per questionnaire Q&A:
  Q# | Questionnaire Type | Question Text | Response Text | Follow-up Question |
  Follow-up Response | Scope ID | Source Table

Used during demos and testing so reviewers can trace every slide bullet back to the input that
produced it.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.utils.dateformat import normalize_dates
from src.utils.logger import get_logger
from src.utils.xml_clean import sanitize_xlsx, xml_safe

logger = get_logger(__name__)

_HEADERS = (
    "Slide",
    "Section",
    "Content",
    "Source Type",
    "Relevant Q#",
    "Question Text",
    "Key Response Extract",
    "Criticality",
    "Content Justification",
)

_Q_HEADERS = (
    "Q#",
    "Questionnaire Type",
    "Question Text",
    "Response Text",
    "Follow-up Question",
    "Follow-up Response",
    "Scope ID",
    "Source Table",
)

_CRITICALITY_LABELS = ("critical:", "major:", "minor:")


def _extract_criticality(text: str) -> str:
    low = text.lower().strip()
    for label in _CRITICALITY_LABELS:
        if low.startswith(label):
            return label.rstrip(":").capitalize()
    return ""


def _strip_criticality(text: str) -> str:
    low = text.lower().strip()
    for label in _CRITICALITY_LABELS:
        if low.startswith(label):
            return text[len(label):].strip()
    return text


def _bullets_to_rows(text: str) -> list[str]:
    lines = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-•* ").strip()
        if s:
            lines.append(s)
    return lines


class ReferenceExcelExporter:
    """Writes the reference workbook for a completed scoping-deck run."""

    def export(
        self,
        content: dict,
        metadata,
        cpr_table,
        gacs_table,
        output_path: str | Path,
        questionnaire_chunks: list | None = None,
    ) -> Path:
        try:
            import openpyxl
            from openpyxl.styles import Alignment, Font, PatternFill
        except ImportError:
            logger.warning("[Excel] openpyxl not installed — skipping reference export")
            return Path(output_path)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Slide Content & Sources"

        header_fill = PatternFill("solid", fgColor="EB1700")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        alt_fill = PatternFill("solid", fgColor="FCE4E4")

        def _write_header(sheet, headers):
            for col, h in enumerate(headers, start=1):
                cell = sheet.cell(row=1, column=col, value=h)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            sheet.row_dimensions[1].height = 28

        def _write_rows(sheet, rows):
            for r_idx, row in enumerate(rows, start=2):
                fill = alt_fill if r_idx % 2 == 0 else None
                for c_idx, val in enumerate(row, start=1):
                    if isinstance(val, str):
                        val = xml_safe(normalize_dates(val))
                    cell = sheet.cell(row=r_idx, column=c_idx, value=val)
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if fill:
                        cell.fill = fill

        # Sheet 1: Slide Content & Sources
        _write_header(ws, _HEADERS)
        rows = self._build_rows(content, metadata, cpr_table, gacs_table, questionnaire_chunks or [])
        _write_rows(ws, rows)
        # Slide | Section | Content | Source Type | Relevant Q# | Question Text | Key Response | Criticality | Justification
        widths = [18, 28, 55, 18, 12, 45, 50, 12, 45]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

        # Sheet 2: Questionnaire Data
        ws2 = wb.create_sheet("Questionnaire Data")
        _write_header(ws2, _Q_HEADERS)
        q_rows = self._build_questionnaire_rows(questionnaire_chunks or [])
        _write_rows(ws2, q_rows)
        q_widths = [8, 20, 55, 55, 45, 45, 18, 30]
        for i, w in enumerate(q_widths, start=1):
            ws2.column_dimensions[ws2.cell(row=1, column=i).column_letter].width = w

        path = Path(output_path)
        wb.save(path)
        sanitize_xlsx(path)  # permanent guarantee: clean the final bytes so Excel never repairs
        logger.info("[Excel] Saved | content_rows=%d | q_rows=%d | path=%s", len(rows), len(q_rows), path)
        return path

    # ── Questionnaire chunk matching ──────────────────────────────────────────

    @staticmethod
    def _chunk_words(chunk) -> set[str]:
        """Significant words (4+ chars) from a chunk's question + response text."""
        text = (
            f"{getattr(chunk, 'question_text', '') or ''} "
            f"{getattr(chunk, 'response_text', '') or ''} "
            f"{getattr(chunk, 'content', '') or ''}"
        )
        return set(re.findall(r'\b[a-zA-Z]{4,}\b', text.lower()))

    def _best_chunk(self, bullet: str, chunks: list):
        """Return the chunk with the highest word-overlap score with the bullet text, or None."""
        if not chunks:
            return None
        bullet_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', bullet.lower()))
        scored = [(len(bullet_words & self._chunk_words(c)), c) for c in chunks]
        scored.sort(key=lambda x: -x[0])
        return scored[0][1] if scored[0][0] > 0 else None

    @staticmethod
    def _q_detail(chunk) -> tuple[str, str, str]:
        """Return (Q#, truncated question text, truncated response text) for a chunk."""
        if chunk is None:
            return ("", "", "")
        q_id = str(getattr(chunk, "question_id", "") or "")
        q_text = str(getattr(chunk, "question_text", "") or getattr(chunk, "content", "") or "")
        r_text = str(getattr(chunk, "response_text", "") or "")
        # Truncate long texts for readability
        q_text = q_text[:300] + "…" if len(q_text) > 300 else q_text
        r_text = r_text[:400] + "…" if len(r_text) > 400 else r_text
        return (q_id, q_text, r_text)

    def _build_rows(self, content: dict, metadata, cpr_table, gacs_table,
                    chunks: list | None = None) -> list[tuple]:
        rows: list[tuple] = []
        entity = getattr(metadata, "audit_entity", "")
        chunks = chunks or []

        def _q_row(bullet: str, slide: str, section: str, source_type: str,
                   justification: str, crit: str = "") -> tuple:
            """Build a row with Q# mapped from best-matching chunk."""
            best = self._best_chunk(bullet, chunks)
            q_id, q_text, r_text = self._q_detail(best)
            return (slide, section, bullet, source_type, q_id, q_text, r_text, crit, justification)

        # ── Slide 2 / 2a / 2b: Market Overview ──────────────────────────────
        for key, label in (
            ("market_overview", f"{entity} Market Overview"),
            ("market_overview_au", "IM Australia Market Overview"),
            ("market_overview_nz", "IM New Zealand Market Overview"),
        ):
            if key not in content or not content[key]:
                continue
            for bullet in _bullets_to_rows(content[key]):
                rows.append(_q_row(
                    bullet, "Market Overview", label,
                    "Questionnaire / Supporting Docs",
                    "Market-level fact abstracted from audit questionnaire context",
                ))

        # ── Slide 3a / 3b: Business Overview ─────────────────────────────────
        bo_sections = {
            "business_overview": "Business Overview",
            "changes_and_challenges": "Changes and Challenges",
            "go_to_market": "Go-to-Market Approach",
            "interactions": "Interactions with Public HCPs/GOs/HCIs",
            "tpi_overview": "TPI Overview",
            "key_regulations": "Key Regulations",
        }
        for suffix, country_label in (("", entity), ("_au", "IM Australia"), ("_nz", "IM New Zealand")):
            for base_key, section_label in bo_sections.items():
                key = f"{base_key}{suffix}"
                if key not in content or not content[key]:
                    continue
                for bullet in _bullets_to_rows(content[key]):
                    rows.append(_q_row(
                        bullet, f"Business Overview ({country_label})", section_label,
                        "Questionnaire / Supporting Docs",
                        "Entity-specific fact grounded in retrieved audit questionnaire context",
                    ))

        # ── Slide 4: Risks noted by Management ───────────────────────────────
        for bullet in _bullets_to_rows(content.get("risks_noted", "")):
            rows.append(_q_row(
                bullet, "Risks noted by Management", "Management Risk Areas",
                "Questionnaire",
                "Risk area explicitly named by a management respondent; not analyst-inferred",
            ))

        # ── Slide 5: Risks noted by Legal Counsel ────────────────────────────
        for bullet in _bullets_to_rows(content.get("legal_risks", "")):
            rows.append((
                "Risks noted by Legal Counsel", "Legal Input", bullet,
                "SI-History & Legal Knowledge Base",
                "", "", "",
                "",
                "Risk/observation noted by Legal Counsel; drawn from legal-input section of SI-history source",
            ))

        # ── Slide 6: SI Investigations Summary ───────────────────────────────
        for bullet in _bullets_to_rows(content.get("si_summary", "")):
            rows.append((
                "SI Investigations Summary", "SI History", bullet,
                "SI-History & Legal Knowledge Base",
                "", "", "",
                "",
                "Confirmed/reported sensitive issue theme; drawn from SI-history section of source",
            ))

        # ── Slide 7: Additional Inputs ───────────────────────────────────────
        for bullet in _bullets_to_rows(content.get("additional_input", "")):
            rows.append((
                "Additional Inputs", "Additional Input", bullet,
                "Additional Input Knowledge Base",
                "", "", "",
                "",
                "Risk/observation noted in the additional inputs provided for this audit",
            ))

        # ── Slide 8: Prior CPR Assessment ────────────────────────────────────
        cpr_year = getattr(cpr_table, "year", "") or ""
        for scope, body in (cpr_table.rows or []):
            for bullet in _bullets_to_rows(body):
                crit = _extract_criticality(bullet)
                clean = _strip_criticality(bullet)
                rows.append((
                    f"Prior CPR Assessment ({cpr_year})", scope, clean,
                    "CPR Reference Documents",
                    "", "", "",
                    crit,
                    f"{crit or 'Observation'} finding from prior Compliance Process Review; "
                    "criticality as recorded in source CPR report",
                ))

        # ── Slide 9: Prior GACS Assessment ───────────────────────────────────
        gacs_year = getattr(gacs_table, "year", "") or ""
        for scope, body in (gacs_table.rows or []):
            for bullet in _bullets_to_rows(body):
                crit = _extract_criticality(bullet)
                clean = _strip_criticality(bullet)
                rows.append((
                    f"Prior GACS Assessment ({gacs_year})", scope, clean,
                    "GACS Reference Documents",
                    "", "", "",
                    crit,
                    f"{crit or 'Observation'} finding from GACS assessment; "
                    "criticality label (Critical/Major/Minor) as recorded in source GACS report",
                ))

        return rows

    @staticmethod
    def _build_questionnaire_rows(chunks: list) -> list[tuple]:
        rows: list[tuple] = []
        for chunk in chunks:
            q_id       = getattr(chunk, "question_id",       "") or ""
            q_type     = getattr(chunk, "questionnaire_type","") or ""
            q_text     = getattr(chunk, "question_text",     "") or ""
            r_text     = getattr(chunk, "response_text",     "") or ""
            fq_text    = getattr(chunk, "followup_question", "") or ""
            fr_text    = getattr(chunk, "followup_response", "") or ""
            scope_id   = getattr(chunk, "scope_id",          "") or ""
            source_tbl = getattr(chunk, "source_table",      "") or ""

            # Fallback: if no structured fields, use the assembled content string
            if not q_text and not r_text:
                r_text = getattr(chunk, "content", "") or ""

            rows.append((
                str(q_id), str(q_type), str(q_text), str(r_text),
                str(fq_text), str(fr_text), str(scope_id), str(source_tbl),
            ))
        return rows
