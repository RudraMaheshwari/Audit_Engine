"""Spec-driven PowerPoint renderer.

Builds every slide in a deck spec into one .pptx with python-pptx (headless and deterministic).
Applies the J&J brand fonts, bulleted body, a common footer, and a locked logo. Adding slides is a
spec change, not a code change here.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from src.utils.logger import get_logger
from src.utils.xml_clean import sanitize_pptx, xml_safe

from .spec import (
    DECK_SPEC,
    SECTION_BREAK,
    SECTION_GAP_PT,
    Alignment,
    DataTableField,
    DynamicTableField,
    FieldKind,
    FieldSpec,
    SlideSpec,
    TableField,
)

logger = get_logger(__name__)

def _safe(text: str) -> str:
    """Strip characters illegal in XML 1.0 (and cap run length) before writing to a slide run — the
    shared cleaner also covers the U+FDD0–FDEF non-characters that corrupt pptx."""
    return xml_safe(text) if text else text


# Capitalise first letter after ': ' and ' – '/' — ' so slide text is consistent.
_CAP_COLON = re.compile(r'(:\**\s+)([a-z])')
_CAP_DASH  = re.compile(r'(\s+[–—]\s+)([a-z])')


def _cap_after_markers(text: str) -> str:
    """Uppercase first char after ': ' or ' – '/' — '."""
    text = _CAP_COLON.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    text = _CAP_DASH.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    return text


_EMU_PER_INCH = 914400

_ALIGNMENT = {
    Alignment.LEFT: PP_ALIGN.LEFT,
    Alignment.CENTER: PP_ALIGN.CENTER,
    Alignment.RIGHT: PP_ALIGN.RIGHT,
    Alignment.JUSTIFY: PP_ALIGN.JUSTIFY,
}
_BULLET_MARKERS = ("- ", "* ", "• ")
_SUB_BULLET_MARKERS = ("  - ", "  * ", "  • ")


class _FooterStyle:
    """Fixed layout and typography for the common slide footer."""

    FONT = "Johnson Text (Body)"
    TOP = 7.05
    HEIGHT = 0.35

    PRIVILEGE_TEXT = "Attorney-Client Privilege"
    ASSURANCE_TEXT = "Global Audit & Assurance"
    MUTED_COLOR = "564C47"

    # Shorthand logo — used on all middle slides. Width auto (None) so the mark keeps its true aspect
    # (not squashed), and TOP is chosen so its vertical centre matches the footer text line
    # (text centre = TOP + HEIGHT/2 = 7.225), keeping the logo on the same line as ACP/GAA/page number.
    LOGO_LEFT = 0.5
    LOGO_HEIGHT = 0.20
    LOGO_TOP = TOP + HEIGHT / 2 - LOGO_HEIGHT / 2   # 7.125
    LOGO_WIDTH = None

    # Single-line "Johnson & Johnson" wordmark — first slide and Thank-you slide. Much wider; height is
    # left to auto (None) so the true aspect ratio is preserved rather than squashed into a small box.
    # Left edge matches the title text (0.7") so the wordmark and the "Thank you" line align vertically.
    # TOP nudged to 7.10 (was 7.02) so the wordmark sits on the same baseline as the footer text.
    WORDMARK_LEFT = 0.7
    WORDMARK_TOP = 7.10
    WORDMARK_WIDTH = 2.2
    WORDMARK_HEIGHT = None

    FALLBACK_TEXT = "J&J"
    FALLBACK_COLOR = "EB1700"
    FALLBACK_SIZE = 16


class DeckRenderer:
    """Renders a deck spec plus a content map into a single PowerPoint file."""

    _SLIDE_WIDTH = Emu(12192000)
    _SLIDE_HEIGHT = Emu(6858000)
    _BULLET_CHAR = "•"
    _BULLET_INDENT = 0.25

    def __init__(self, spec: tuple[SlideSpec, ...] = DECK_SPEC) -> None:
        self._spec = spec

    def render(
        self,
        content: dict,
        output_path: str | Path,
        context: dict | None = None,
        logo_path: str = "",
        opening_logo_path: str = "",
        closing_logo_path: str = "",
    ) -> Path:
        """Render every slide into one .pptx and return the saved path."""
        context = context or {}
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        presentation = Presentation()
        presentation.slide_width = self._SLIDE_WIDTH
        presentation.slide_height = self._SLIDE_HEIGHT
        page_no = 0
        for slide_spec in self._spec:
            if not self._has_content(slide_spec, content):
                logger.info("[Deck] Skipping empty slide: %s", slide_spec.name)
                continue
            # Paginate slides with DataTableField overflow — each page gets its own
            # content dict override with the row slice for that page.
            for _page_spec, page_content in self._paginated_slides(slide_spec, content):
                page_no += 1
                footer_mode = getattr(_page_spec, "footer", "standard")
                # Logo per slide: closing (white single-line) on the Thank-you slide; single-line red on
                # the first slide; shorthand everywhere else. The single-line logos are the wide wordmark,
                # so they take the wider dimensions; the shorthand keeps its small square box.
                if footer_mode == "closing" and closing_logo_path and Path(closing_logo_path).is_file():
                    slide_logo, wordmark = closing_logo_path, True
                elif page_no == 1 and opening_logo_path and Path(opening_logo_path).is_file():
                    slide_logo, wordmark = opening_logo_path, True
                else:
                    slide_logo, wordmark = logo_path, False
                logo_dims = self._logo_dims(wordmark)
                self._render_slide(
                    presentation, _page_spec, page_content, context, page_no, slide_logo, footer_mode, logo_dims
                )

        presentation.save(path)
        sanitize_pptx(path)  # permanent backstop: clean the final bytes so PowerPoint never repairs
        logger.info("[Deck] Rendered %d slide(s) -> %s", page_no, path)
        return path

    def _paginated_slides(
        self, spec: SlideSpec, content: dict
    ) -> list[tuple[SlideSpec, dict]]:
        """Split a slide with a DataTableField into multiple pages if row count exceeds the limit.

        Returns a list of (SlideSpec, content_override) pairs. When no pagination is needed the
        original spec and content are returned as a single-element list.
        """
        # Find the first DataTableField with pagination enabled.
        paginated_field: DataTableField | None = None
        for field in spec.fields:
            if isinstance(field, DataTableField) and field.max_rows_per_slide > 0:
                paginated_field = field
                break

        if paginated_field is None:
            return [(spec, content)]

        all_rows = content.get(paginated_field.content_key) or []
        max_per = paginated_field.max_rows_per_slide
        if not all_rows:
            return [(spec, content)]

        # Content-aware pagination: a row with a long Comments cell is much taller than a short one,
        # so splitting purely on row count overflows the slide. Estimate each row's rendered line
        # count from its cell text + column widths and pack rows until the table's vertical budget
        # (or the max-rows cap) is reached.
        chunks = self._pack_rows_by_height(all_rows, paginated_field)
        total_pages = len(chunks)
        if total_pages <= 1:
            return [(spec, content)]
        pages: list[tuple[SlideSpec, dict]] = []
        for page_idx, chunk in enumerate(chunks, start=1):
            page_content = {**content, paginated_field.content_key: chunk}
            if total_pages > 1:
                # Add " (cont.)" suffix to title on continuation slides; update the
                # title field's text_template in the spec for page 2+.
                if page_idx > 1:
                    new_fields = []
                    for f in spec.fields:
                        if isinstance(f, FieldSpec) and f.kind is FieldKind.TITLE:
                            # Append " (cont.)" to the title template.
                            new_fields.append(FieldSpec(
                                kind=f.kind, font_name=f.font_name, font_size=f.font_size,
                                color_hex=f.color_hex, bold=f.bold, left=f.left, top=f.top,
                                width=f.width, height=f.height,
                                text_template=f.text_template + " (cont.)",
                                content_key=f.content_key, bullets=f.bullets, align=f.align,
                                skip_if_empty=f.skip_if_empty,
                            ))
                        else:
                            new_fields.append(f)
                    page_spec = SlideSpec(
                        name=f"{spec.name} (page {page_idx})",
                        fields=tuple(new_fields),
                        layout_index=spec.layout_index,
                        background_hex=spec.background_hex,
                        footer=spec.footer,
                    )
                else:
                    page_spec = spec
            else:
                page_spec = spec
            pages.append((page_spec, page_content))

        logger.info(
            "[Deck] Paginated '%s' into %d slide(s) (%d rows, content-aware; cap %d/slide)",
            spec.name, total_pages, len(all_rows), max_per,
        )
        return pages

    # Average characters per inch of usable column width for the table cell font (~11pt proportional).
    _CHARS_PER_INCH = 12.5
    _CELL_PADDING_IN = 0.16          # left+right cell padding eats into wrap width
    _LINE_HEIGHT_FACTOR = 1.45       # pt line box ÷ pt font size (PPT table rows run ~1.4–1.5×)
    _HEADER_HEIGHT_IN = 0.95         # header row + a bottom safety margin so the table never spills off
    _ROW_OVERHEAD_LINES = 0.5        # per-row top+bottom cell padding, counted so multi-row slides fit

    @classmethod
    def _chars_per_line(cls, col_w: float) -> int:
        return max(6, int((col_w - cls._CELL_PADDING_IN) * cls._CHARS_PER_INCH))

    @classmethod
    def _cell_lines(cls, value, col_w: float) -> int:
        """Wrapped visual line count for one cell's text at the given column width."""
        lines = 0
        for segment in str(value or "").split("\n"):
            seg = segment.rstrip()
            lines += math.ceil(len(seg) / cls._chars_per_line(col_w)) if seg else 1
        return max(1, lines)

    @classmethod
    def _row_lines(cls, row, col_widths: tuple[float, ...]) -> int:
        """A row's height = its tallest cell."""
        return max((cls._cell_lines(v, w) for v, w in zip(row, col_widths)), default=1)

    @classmethod
    def _split_tall_row(cls, row, col_widths, budget, first_budget) -> list:
        """Split one row whose Comments cell is too tall into continuation rows.

        The Comments column (last column) is flowed across slides: the first piece keeps the full row
        (scope area, sample, txn totals); continuation pieces repeat only the scope-area name with the
        remaining comment bullets, so the SAME scope reads across consecutive slides instead of being
        crammed onto one. Each piece is sized to fill its slide.
        """
        n = len(row)
        comment_idx = len(col_widths) - 1
        comment_w = col_widths[comment_idx]
        # Non-comment columns' height (scope area may wrap to 2 lines) — reserved on every piece.
        base_lines = max(
            (cls._cell_lines(row[c], col_widths[c]) for c in range(n) if c != comment_idx),
            default=1,
        )
        source_lines = str(row[comment_idx] or "").split("\n")

        pieces: list = []
        i = 0
        first = True
        while i < len(source_lines):
            slide_budget = first_budget if first else budget
            cap = max(1, slide_budget - base_lines)
            used = 0
            chunk: list[str] = []
            while i < len(source_lines):
                seg = source_lines[i].rstrip()
                seg_lines = cls._cell_lines(seg, comment_w) if seg else 1
                if chunk and used + seg_lines > cap:
                    break
                chunk.append(source_lines[i])
                used += seg_lines
                i += 1
            piece_comment = "\n".join(chunk)
            if first:
                piece = tuple(piece_comment if c == comment_idx else row[c] for c in range(n))
            else:
                piece = tuple(
                    row[0] if c == 0 else (piece_comment if c == comment_idx else "")
                    for c in range(n)
                )
            pieces.append(piece)
            first = False
        return pieces

    @classmethod
    def _pack_rows_by_height(cls, all_rows: list, field: DataTableField) -> list[list]:
        """Flow rows into pages by estimated rendered height.

        Small scopes pack together to fill a slide; a scope whose Comments overflow a whole slide is
        split across consecutive slides (continuation rows) rather than getting a lonely, half-empty
        slide of its own.
        """
        line_height_in = (field.cell_size * cls._LINE_HEIGHT_FACTOR) / 72.0
        usable_in = max(line_height_in, field.height - cls._HEADER_HEIGHT_IN)
        budget = max(1, int(usable_in / line_height_in))
        cap = field.max_rows_per_slide or len(all_rows)

        pages: list[list] = []
        current: list = []
        used = 0

        def flush():
            nonlocal current, used
            if current:
                pages.append(current)
                current, used = [], 0

        for row in all_rows:
            row_lines = cls._row_lines(row, field.col_widths)

            if row_lines <= budget:
                # Each row also costs top+bottom cell padding — count it so several rows don't overflow.
                cost = row_lines + cls._ROW_OVERHEAD_LINES
                if current and (used + cost > budget or len(current) >= cap):
                    flush()
                current.append(row)
                used += cost
                continue

            # Row taller than a whole slide → flow its Comments across slides. Use the remaining space
            # on the current slide for the first piece, then full slides for the rest.
            first_budget = budget - used if current else budget
            if first_budget < budget // 3:        # too little room left — start the big row fresh
                flush()
                first_budget = budget
            pieces = cls._split_tall_row(row, field.col_widths, budget, first_budget)
            for idx, piece in enumerate(pieces):
                current.append(piece)
                if idx < len(pieces) - 1:
                    flush()                        # every piece but the last fills its own slide
                else:
                    # last piece can accept more rows — include its per-row overhead too
                    used = cls._row_lines(piece, field.col_widths) + cls._ROW_OVERHEAD_LINES

        if current:
            pages.append(current)
        return pages

    @staticmethod
    def _logo_dims(wordmark: bool) -> tuple[float, float, float, float | None]:
        """(left, top, width, height) for the chosen logo; height None = preserve aspect ratio."""
        if wordmark:
            return (_FooterStyle.WORDMARK_LEFT, _FooterStyle.WORDMARK_TOP,
                    _FooterStyle.WORDMARK_WIDTH, _FooterStyle.WORDMARK_HEIGHT)
        return (_FooterStyle.LOGO_LEFT, _FooterStyle.LOGO_TOP,
                _FooterStyle.LOGO_WIDTH, _FooterStyle.LOGO_HEIGHT)

    def _render_slide(self, presentation, spec: SlideSpec, content: dict, context: dict,
                      page_no: int, logo_path: str, footer_mode: str = "standard",
                      logo_dims: tuple | None = None) -> None:
        slide = presentation.slides.add_slide(presentation.slide_layouts[spec.layout_index])
        # Full-bleed background colour first (so all fields render on top of it).
        if getattr(spec, "background_hex", ""):
            self._apply_background(slide, spec.background_hex)
        for field in spec.fields:
            if field.kind is FieldKind.TITLE:
                self._render_title(slide, field, context)
            elif field.kind is FieldKind.BODY:
                self._render_body(slide, field, content)
            elif field.kind is FieldKind.TEXT:
                self._render_text(slide, field, context)
            elif field.kind is FieldKind.TABLE:
                self._render_table(slide, field, content, context)
            elif field.kind is FieldKind.DYNAMIC_TABLE:
                self._render_dynamic_table(slide, field, content, context)
            elif field.kind is FieldKind.DATA_TABLE:
                self._render_data_table(slide, field, content)
        self._render_footer(slide, page_no, logo_path, footer_mode, logo_dims)

    def _apply_background(self, slide, hex_color: str) -> None:
        """Draw a full-bleed rectangle as the slide background (added first, so it sits behind
        every field). More reliable across python-pptx versions than SlideBackground fills."""
        rect = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, 0, 0, self._SLIDE_WIDTH, self._SLIDE_HEIGHT
        )
        rect.fill.solid()
        rect.fill.fore_color.rgb = RGBColor.from_string(hex_color)
        rect.line.fill.background()
        rect.shadow.inherit = False

    @staticmethod
    def _has_content(spec: SlideSpec, content: dict) -> bool:
        """Skip a slide with no content to show: a dynamic table with no rows (e.g. no CPR/GACS
        findings), or a field flagged skip_if_empty whose content is empty (e.g. the 'Risks noted by
        Management' slide when management noted no risks)."""
        for field in spec.fields:
            kind = getattr(field, "kind", None)
            if kind in (FieldKind.DYNAMIC_TABLE, FieldKind.DATA_TABLE):
                if not content.get(field.content_key):
                    return False
            if getattr(field, "skip_if_empty", False):
                val = content.get(field.content_key)
                if isinstance(val, str):
                    val = val.strip()
                if not val:
                    return False
        return True

    def _render_dynamic_table(self, slide, field: DynamicTableField, content: dict, context: dict) -> None:
        rows_data = content.get(field.content_key) or []
        shape = slide.shapes.add_table(
            len(rows_data) + 1, 2,
            self._emu(field.left), self._emu(field.top),
            self._emu(field.width), self._emu(field.height),
        )
        table = shape.table
        table.first_row = True
        table.horz_banding = False
        table.columns[0].width = self._emu(field.topic_col_width)
        table.columns[1].width = self._emu(field.width - field.topic_col_width)

        header = table.cell(0, 0)
        header.merge(table.cell(0, 1))
        self._write_header_cell(header, field.header_template.format(**context), field)
        table.rows[0].height = self._emu(0.4)

        for index, (topic, body) in enumerate(rows_data, start=1):
            self._write_topic_cell(table.cell(index, 0), topic, field)
            self._write_content_cell(table.cell(index, 1), body, field)

    def _render_data_table(self, slide, field: DataTableField, content: dict) -> None:
        """Render a fixed-header, multi-column table whose rows come from generated cell tuples."""
        rows_data = content.get(field.content_key) or []
        if not rows_data:
            return
        n_cols = len(field.headers)
        shape = slide.shapes.add_table(
            len(rows_data) + 1, n_cols,
            self._emu(field.left), self._emu(field.top),
            self._emu(field.width), self._emu(field.height),
        )
        table = shape.table
        table.first_row = True
        table.horz_banding = False
        for i, w in enumerate(field.col_widths[:n_cols]):
            table.columns[i].width = self._emu(w)

        # Header row — red fill, white bold, centred.
        for c, label in enumerate(field.headers):
            cell = table.cell(0, c)
            self._style_data_cell(cell, field.header_fill_hex)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            para = cell.text_frame.paragraphs[0]
            para.alignment = PP_ALIGN.CENTER
            run = para.add_run()
            run.text = _safe(str(label))
            self._run_font(run, field.header_font, field.header_size, field.header_color_hex, bold=True)
        table.rows[0].height = self._emu(0.5)

        # Data rows — alternating rose shades; first column emphasised, others regular.
        for r, row in enumerate(rows_data, start=1):
            fill = field.row_fill_a_hex if (r % 2 == 1) else field.row_fill_b_hex
            for c in range(n_cols):
                value = str(row[c]) if c < len(row) and row[c] is not None else ""
                cell = table.cell(r, c)
                self._style_data_cell(cell, fill)
                cell.vertical_anchor = MSO_ANCHOR.TOP
                if c == 0:
                    font = (field.first_col_font, field.first_col_size, field.first_col_color_hex, True)
                else:
                    font = (field.cell_font, field.cell_size, field.cell_color_hex, False)
                # Multi-line cell text (e.g. bulleted Comments) must become one paragraph per line —
                # a raw '\n' inside a single run writes an invalid <a:t> and makes PowerPoint "repair".
                lines = value.splitlines() or [""]
                for index, line in enumerate(lines):
                    para = cell.text_frame.paragraphs[0] if index == 0 else cell.text_frame.add_paragraph()
                    if not line:
                        continue   # leave an EMPTY paragraph, never an empty run (PowerPoint repairs those)
                    run = para.add_run()
                    run.text = _safe(line)
                    self._run_font(run, font[0], font[1], font[2], bold=font[3])

    def _style_data_cell(self, cell, fill_hex: str) -> None:
        self._set_cell_borders(cell)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(fill_hex)
        cell.margin_left = self._emu(0.07)
        cell.margin_right = self._emu(0.07)
        cell.margin_top = self._emu(0.03)
        cell.margin_bottom = self._emu(0.03)

    def _render_table(self, slide, field: TableField, content: dict, context: dict) -> None:
        shape = slide.shapes.add_table(
            len(field.rows) + 1, 2,
            self._emu(field.left), self._emu(field.top),
            self._emu(field.width), self._emu(field.height),
        )
        table = shape.table
        table.first_row = True
        table.horz_banding = False
        table.columns[0].width = self._emu(field.topic_col_width)
        table.columns[1].width = self._emu(field.width - field.topic_col_width)

        header = table.cell(0, 0)
        header.merge(table.cell(0, 1))
        self._write_header_cell(header, field.header_template.format(**context), field)
        table.rows[0].height = self._emu(0.4)

        for index, row in enumerate(field.rows, start=1):
            self._write_topic_cell(table.cell(index, 0), row.topic, field)
            self._write_content_cell(table.cell(index, 1), content.get(row.content_key, ""), field)

    def _write_header_cell(self, cell, text: str, field: TableField) -> None:
        self._style_cell(cell, field.header_fill_hex)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        paragraph = cell.text_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        run = paragraph.add_run()
        run.text = _safe(text)
        self._run_font(run, field.header_font, field.header_size, field.header_color_hex, bold=True)

    def _write_topic_cell(self, cell, text: str, field: TableField) -> None:
        self._style_cell(cell, "FFFFFF")
        cell.vertical_anchor = MSO_ANCHOR.TOP
        if not (text and text.strip()):
            return   # leave the cell's empty paragraph, never an empty run
        run = cell.text_frame.paragraphs[0].add_run()
        run.text = _safe(text)
        self._run_font(run, field.topic_font, field.topic_size, field.topic_color_hex, bold=True)

    def _write_content_cell(self, cell, text: str, field: TableField) -> None:
        self._style_cell(cell, "FFFFFF")
        cell.vertical_anchor = MSO_ANCHOR.TOP
        lines = [ln for ln in (text or "").splitlines() if ln.strip()]
        for index, line in enumerate(lines):
            paragraph = cell.text_frame.paragraphs[0] if index == 0 else cell.text_frame.add_paragraph()
            paragraph.space_before = Pt(0)
            paragraph.space_after = Pt(2)
            is_bullet = self._has_marker(line)
            cleaned = self._strip_bullet(line) if is_bullet else line.rstrip()
            cleaned = _cap_after_markers(cleaned)
            for part_index, part in enumerate(cleaned.split("**")):
                if not part:
                    continue
                run = paragraph.add_run()
                run.text = _safe(part)
                self._run_font(
                    run, field.content_font, field.content_size, field.content_color_hex,
                    bold=(part_index % 2 == 1),
                )
            if is_bullet:
                self._set_bullet(paragraph)

    @staticmethod
    def _run_font(run, name: str, size: int, color_hex: str, *, bold: bool) -> None:
        run.font.name = name
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color_hex)

    def _style_cell(self, cell, fill_hex: str) -> None:
        self._set_cell_borders(cell)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(fill_hex)
        cell.margin_left = self._emu(0.07)
        cell.margin_right = self._emu(0.07)
        cell.margin_top = self._emu(0.03)
        cell.margin_bottom = self._emu(0.03)

    @staticmethod
    def _set_cell_borders(cell, color_hex: str = "C9422F", width_emu: int = 6350) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
            for existing in tc_pr.findall(qn(tag)):
                tc_pr.remove(existing)
        for tag in ("a:lnB", "a:lnT", "a:lnR", "a:lnL"):
            line = tc_pr.makeelement(qn(tag), {"w": str(width_emu), "cap": "flat"})
            solid = tc_pr.makeelement(qn("a:solidFill"), {})
            color = tc_pr.makeelement(qn("a:srgbClr"), {"val": color_hex})
            solid.append(color)
            line.append(solid)
            tc_pr.insert(0, line)

    def _render_text(self, slide, field: FieldSpec, context: dict) -> None:
        text_frame = self._add_textbox(slide, field.left, field.top, field.width, field.height)
        text = field.text_template.format(**context).replace("_", " ")
        for index, line in enumerate(text.split("\n")):
            paragraph = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
            paragraph.alignment = _ALIGNMENT[field.align]
            if not line:
                continue   # empty paragraph, never an empty run (PowerPoint repairs those)
            run = paragraph.add_run()
            run.text = _safe(line)
            self._apply_font(run, field)

    def _render_title(self, slide, field: FieldSpec, context: dict) -> None:
        text_frame = self._add_textbox(slide, field.left, field.top, field.width, field.height)
        # Titles must never wrap: keep the full heading on one line at its given size (business-fixed),
        # matching the wireframe. Text extends as needed rather than dropping to a clipped second line.
        text_frame.word_wrap = False
        title = field.text_template.format(**context).replace("_", " ")
        paragraph = text_frame.paragraphs[0]
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(12)
        run = paragraph.add_run()
        run.text = _safe(title)
        self._apply_font(run, field)

    # Body auto-fit tuning — shrink long body text so it never overflows its box.
    _BODY_MIN_SCALE = 0.55           # never shrink body text below 55% of its spec size
    _BODY_LINE_FACTOR = 1.22         # line box ÷ font size for a body paragraph
    _BODY_SPACE_AFTER_PT = 2

    @classmethod
    def _body_fit_scale(cls, lines: list[str], field: FieldSpec) -> float:
        """Estimate a font scale (<=1.0) so the body text fits field.height; 1.0 when it already fits."""
        base = float(field.font_size)
        if base <= 0:
            return 1.0
        chars_per_line = max(6, int((field.width - 0.1) * (150.0 / base)))  # ~glyph width ∝ font size
        line_h_in = base * cls._BODY_LINE_FACTOR / 72.0
        gap_in = cls._BODY_SPACE_AFTER_PT / 72.0
        total = 0.0
        for ln in lines:
            s = ln.strip()
            if s == SECTION_BREAK:
                total += SECTION_GAP_PT / 72.0
                continue
            total += max(1, math.ceil(len(s) / chars_per_line)) * line_h_in + gap_in
        if total <= field.height or total <= 0:
            return 1.0
        # A font scale shrinks BOTH line width and line height, so height ≈ scale² — sqrt fits in one pass.
        return max(cls._BODY_MIN_SCALE, (field.height / total) ** 0.5)

    def _render_body(self, slide, field: FieldSpec, content: dict) -> None:
        text = (content.get(field.content_key) or "").strip()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return

        text_frame = self._add_textbox(slide, field.left, field.top, field.width, field.height)
        # PowerPoint shrink-to-fit as a safety net on top of our own scaling (only ever shrinks).
        try:
            text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        except Exception:  # noqa: BLE001 — not all python-pptx versions expose this
            pass
        scale = self._body_fit_scale(lines, field)
        first = True
        pending_gap = 0
        for line in lines:
            if line.strip() == SECTION_BREAK:
                pending_gap = SECTION_GAP_PT
                continue
            is_sub = self._has_sub_marker(line)
            is_bullet = field.bullets or self._has_marker(line)
            paragraph = text_frame.paragraphs[0] if first else text_frame.add_paragraph()
            first = False
            paragraph.alignment = _ALIGNMENT[field.align]
            paragraph.space_before = Pt(pending_gap * scale)
            paragraph.space_after = Pt(self._BODY_SPACE_AFTER_PT * scale)
            pending_gap = 0
            if is_sub:
                cleaned = _cap_after_markers(self._strip_sub_bullet(line))
                self._add_runs(paragraph, cleaned, field, scale)
                self._set_sub_bullet(paragraph)
            else:
                cleaned = self._strip_bullet(line) if is_bullet else line.rstrip()
                cleaned = _cap_after_markers(cleaned)
                self._add_runs(paragraph, cleaned, field, scale)
                if is_bullet:
                    self._set_bullet(paragraph)

    @staticmethod
    def _has_marker(line: str) -> bool:
        stripped = line.lstrip()
        return stripped.startswith(_BULLET_MARKERS)

    @staticmethod
    def _has_sub_marker(line: str) -> bool:
        return line.startswith(_SUB_BULLET_MARKERS)

    def _set_sub_bullet(self, paragraph) -> None:
        """Indented (sub-)bullet — deeper left margin, same bullet char as top-level."""
        sub_indent = self._BULLET_INDENT * 2
        properties = paragraph._p.get_or_add_pPr()
        properties.set("marL", str(int(sub_indent * _EMU_PER_INCH)))
        properties.set("indent", str(int(-self._BULLET_INDENT * _EMU_PER_INCH)))
        for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
            existing = properties.find(qn(tag))
            if existing is not None:
                properties.remove(existing)
        properties.append(properties.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
        properties.append(properties.makeelement(qn("a:buChar"), {"char": "–"}))

    @staticmethod
    def _strip_sub_bullet(line: str) -> str:
        return line.lstrip().lstrip("•-*–").strip()

    def _render_footer(self, slide, page_no: int, logo_path: str, mode: str = "standard",
                       logo_dims: tuple | None = None) -> None:
        if mode == "none":
            return
        # Both standard and closing footers show the logo (closing uses the white single-line logo,
        # already selected by the caller). Only the standard footer adds the privilege/GAA/page text.
        self._render_logo(slide, logo_path, logo_dims)
        if mode == "closing":
            return
        self._footer_text(slide, _FooterStyle.PRIVILEGE_TEXT, 4.0, 5.3, 14,
                          _FooterStyle.MUTED_COLOR, align=PP_ALIGN.CENTER)
        self._footer_text(slide, _FooterStyle.ASSURANCE_TEXT, 8.8, 3.3, 14,
                          _FooterStyle.MUTED_COLOR, align=PP_ALIGN.RIGHT)
        # Opening slide (page 1) shows no number; the closing slide returns before this point.
        # Visible numbering starts at 1 on the first content slide (page 2 internally → displays "1").
        if page_no > 1:
            self._footer_text(slide, str(page_no - 1), 12.3, 0.8, 9, "000000",
                              align=PP_ALIGN.RIGHT, font="Johnson Text")

    def _render_logo(self, slide, logo_path: str, logo_dims: tuple | None = None) -> None:
        left, top, width, height = logo_dims or (
            _FooterStyle.LOGO_LEFT, _FooterStyle.LOGO_TOP,
            _FooterStyle.LOGO_WIDTH, _FooterStyle.LOGO_HEIGHT,
        )
        if logo_path and Path(logo_path).is_file():
            try:
                # Pass only the dimension(s) provided; a None dimension lets python-pptx preserve the
                # image's true aspect ratio (avoids squashing the mark).
                kwargs = {}
                if width is not None:
                    kwargs["width"] = self._emu(width)
                if height is not None:
                    kwargs["height"] = self._emu(height)
                if not kwargs:
                    kwargs["height"] = self._emu(_FooterStyle.LOGO_HEIGHT)
                picture = slide.shapes.add_picture(
                    logo_path, self._emu(left), self._emu(top), **kwargs
                )
                self._lock_picture(picture)
                return
            except Exception as exc:
                logger.warning("[Deck] Could not place logo %s — using text | %s", logo_path, exc)
        self._footer_text(
            slide, _FooterStyle.FALLBACK_TEXT, 0.5, 2.0, _FooterStyle.FALLBACK_SIZE,
            _FooterStyle.FALLBACK_COLOR, bold=True, align=PP_ALIGN.LEFT,
        )

    def _footer_text(self, slide, text: str, left: float, width: float, size: int, color: str,
                     *, bold: bool = False, align=PP_ALIGN.LEFT, font: str = _FooterStyle.FONT) -> None:
        box = slide.shapes.add_textbox(
            self._emu(left), self._emu(_FooterStyle.TOP), self._emu(width), self._emu(_FooterStyle.HEIGHT)
        )
        text_frame = box.text_frame
        text_frame.word_wrap = True
        # Center text vertically in the fixed-height footer box and zero the insets, so items of
        # different font sizes (e.g. the 9pt page number vs the 14pt ACP/GAA text) share ONE baseline
        # line aligned with the logo, instead of aligning at their tops.
        text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        text_frame.margin_top = Emu(0)
        text_frame.margin_bottom = Emu(0)
        paragraph = text_frame.paragraphs[0]
        paragraph.alignment = align
        run = paragraph.add_run()
        run.text = _safe(text)
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)

    def _add_textbox(self, slide, left: float, top: float, width: float, height: float):
        box = slide.shapes.add_textbox(
            self._emu(left), self._emu(top), self._emu(width), self._emu(height)
        )
        box.text_frame.word_wrap = True
        return box.text_frame

    @staticmethod
    def _apply_font(run, field: FieldSpec, size_scale: float = 1.0) -> None:
        run.font.name = field.font_name
        run.font.size = Pt(field.font_size * size_scale)
        run.font.bold = field.bold
        run.font.color.rgb = RGBColor.from_string(field.color_hex)

    def _add_runs(self, paragraph, text: str, field: FieldSpec, size_scale: float = 1.0) -> None:
        for index, part in enumerate(text.split("**")):
            if not part:
                continue
            run = paragraph.add_run()
            run.text = _safe(part)
            self._apply_font(run, field, size_scale)
            if index % 2 == 1:
                run.font.bold = True

    def _set_bullet(self, paragraph) -> None:
        properties = paragraph._p.get_or_add_pPr()
        properties.set("marL", str(int(self._BULLET_INDENT * _EMU_PER_INCH)))
        properties.set("indent", str(int(-self._BULLET_INDENT * _EMU_PER_INCH)))
        for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
            existing = properties.find(qn(tag))
            if existing is not None:
                properties.remove(existing)
        properties.append(properties.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
        properties.append(properties.makeelement(qn("a:buChar"), {"char": self._BULLET_CHAR}))

    @staticmethod
    def _lock_picture(picture) -> None:
        nv_pic_pr = picture._element.find(qn("p:nvPicPr"))
        c_nv_pic_pr = nv_pic_pr.find(qn("p:cNvPicPr")) if nv_pic_pr is not None else None
        if c_nv_pic_pr is None:
            return
        # add_picture ALREADY inserts <a:picLocks noChangeAspect="1"/>. Reuse it — a SECOND picLocks
        # element in cNvPicPr is a schema violation that makes PowerPoint 'repair' every slide. Only
        # create one if (unexpectedly) absent, then set all the lock flags on the single element.
        locks = c_nv_pic_pr.find(qn("a:picLocks"))
        if locks is None:
            locks = c_nv_pic_pr.makeelement(qn("a:picLocks"), {})
            c_nv_pic_pr.insert(0, locks)
        for attr in ("noMove", "noResize", "noSelect", "noChangeAspect"):
            locks.set(attr, "1")

    @staticmethod
    def _strip_bullet(line: str) -> str:
        return line.lstrip().lstrip("•-*").strip()

    @staticmethod
    def _emu(inches: float) -> Emu:
        return Emu(int(inches * _EMU_PER_INCH))
