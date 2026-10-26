"""Declarative deck template: slides described as data so new slides are a spec change, not code.

Coordinates are in inches; the renderer converts to EMU. Colours are hex strings without a leading
hash. Adding slides is adding SlideSpec entries to DECK_SPEC.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FieldKind(Enum):
    """The role a field plays on a slide."""

    TITLE = "title"
    BODY = "body"
    TEXT = "text"
    TABLE = "table"
    DYNAMIC_TABLE = "dynamic_table"
    DATA_TABLE = "data_table"


class Alignment(Enum):
    """Horizontal text alignment for a field."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


@dataclass(frozen=True)
class FieldSpec:
    """Formatting and content binding for a single field on a slide."""

    kind: FieldKind
    font_name: str
    font_size: int
    color_hex: str
    bold: bool = False
    left: float = 0.5
    top: float = 0.3
    width: float = 12.3
    height: float = 1.2
    text_template: str = ""
    content_key: str = ""
    bullets: bool = False
    align: Alignment = Alignment.LEFT
    # When True, the whole slide is skipped if this field's content_key resolves to empty content
    # (e.g. the 'Risks noted by Management' slide only appears when management actually noted risks).
    skip_if_empty: bool = False


@dataclass(frozen=True)
class TableRow:
    """One data row of a two-column table: a fixed topic label and its content key."""

    topic: str
    content_key: str


@dataclass(frozen=True)
class TableField:
    """A two-column table with a merged header row (topic | AI-generated content)."""

    kind: FieldKind
    header_template: str
    rows: tuple[TableRow, ...]
    left: float
    top: float
    width: float
    height: float
    topic_col_width: float
    header_font: str
    header_size: int
    header_color_hex: str
    header_fill_hex: str
    topic_font: str
    topic_size: int
    topic_color_hex: str
    content_font: str
    content_size: int
    content_color_hex: str


@dataclass(frozen=True)
class DynamicTableField:
    """A two-column table whose rows come from generated content at render time (topic | body)."""

    kind: FieldKind
    header_template: str
    content_key: str
    left: float
    top: float
    width: float
    height: float
    topic_col_width: float
    header_font: str
    header_size: int
    header_color_hex: str
    header_fill_hex: str
    topic_font: str
    topic_size: int
    topic_color_hex: str
    content_font: str
    content_size: int
    content_color_hex: str


@dataclass(frozen=True)
class DataTableField:
    """A multi-column table whose header labels are fixed and whose rows come from generated content
    (a list of same-length cell tuples under content_key). First column is emphasised; remaining
    columns are regular; rows alternate two fill shades."""

    kind: FieldKind
    headers: tuple[str, ...]
    content_key: str
    col_widths: tuple[float, ...]   # inches per column (should sum to ~width)
    left: float
    top: float
    width: float
    height: float
    header_font: str
    header_size: int
    header_color_hex: str
    header_fill_hex: str
    first_col_font: str
    first_col_size: int
    first_col_color_hex: str
    cell_font: str
    cell_size: int
    cell_color_hex: str
    row_fill_a_hex: str = "F4CCCC"
    row_fill_b_hex: str = "FCE4E4"
    skip_if_empty: bool = True
    # Maximum data rows per slide before overflowing onto a continuation slide.
    # 0 = unlimited (all rows on one slide, legacy behaviour).
    max_rows_per_slide: int = 0


@dataclass(frozen=True)
class SlideSpec:
    """A single slide: its fields and the layout to base it on (6 = blank)."""

    name: str
    fields: tuple
    layout_index: int = 6
    # Optional full-bleed background colour (hex, no '#'); "" = default white.
    background_hex: str = ""
    # Footer style: "standard" (logo + privilege/GAA/page no.), "closing" (single-line white logo
    # only, no text/page number — the Thank-you slide), or "none".
    footer: str = "standard"


_HEADER_FONT = "Johnson Display (Headings)"
_HEADER_COLOR = "EB1700"
_BODY_FONT = "Johnson Text (Body)"
_BODY_COLOR = "000000"

# Marker inserted between merged body sections; the renderer adds SECTION_GAP_PT before the next line.
SECTION_BREAK = "[[SECTION_BREAK]]"
SECTION_GAP_PT = 8


OPENING_SLIDE = SlideSpec(
    name="Opening",
    fields=(
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_HEADER_FONT, font_size=60, color_hex=_HEADER_COLOR, bold=True,
            left=0.6, top=0.6, width=12.2, height=2.6,
            text_template="{audit_entity} {mrc_number}\nCompliance Process Review",
            align=Alignment.LEFT,
        ),
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_BODY_FONT, font_size=28, color_hex=_BODY_COLOR, bold=False,
            left=0.6, top=3.6, width=12.0, height=1.4,
            text_template="Scope Discussion\nReview Period : {scope_period}",
            align=Alignment.LEFT,
        ),
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_BODY_FONT, font_size=28, color_hex=_BODY_COLOR, bold=False,
            left=0.6, top=5.3, width=12.0, height=0.8,
            text_template="{current_date}", align=Alignment.LEFT,
        ),
    ),
)

MARKET_OVERVIEW_SLIDE = SlideSpec(
    name="Market Overview",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="{entity} Market Overview",
        ),
        FieldSpec(
            kind=FieldKind.BODY,
            font_name=_BODY_FONT, font_size=14, color_hex=_BODY_COLOR, bold=False,
            # top nudged up so the header→first-bullet gap is ~6pt (was ~12pt at top=1.1).
            left=0.5, top=0.9, width=12.3, height=5.85,
            content_key="market_overview", bullets=False, align=Alignment.JUSTIFY,
        ),
    ),
)

# Sam_1 only: split Market Overview into Australia and New Zealand slides.
MARKET_OVERVIEW_AU_SLIDE = SlideSpec(
    name="Market Overview — Australia",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="IM Australia Market Overview",
        ),
        FieldSpec(
            kind=FieldKind.BODY,
            font_name=_BODY_FONT, font_size=14, color_hex=_BODY_COLOR, bold=False,
            left=0.5, top=0.9, width=12.3, height=5.85,
            content_key="market_overview_au", bullets=False, align=Alignment.JUSTIFY,
        ),
    ),
)

MARKET_OVERVIEW_NZ_SLIDE = SlideSpec(
    name="Market Overview — New Zealand",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="IM New Zealand Market Overview",
        ),
        FieldSpec(
            kind=FieldKind.BODY,
            font_name=_BODY_FONT, font_size=14, color_hex=_BODY_COLOR, bold=False,
            left=0.5, top=0.9, width=12.3, height=5.85,
            content_key="market_overview_nz", bullets=False, align=Alignment.JUSTIFY,
        ),
    ),
)

def _business_table(header: str) -> TableField:
    return TableField(
        kind=FieldKind.TABLE,
        header_template=header,
        rows=(
            TableRow("Business Overview", "business_overview"),
            TableRow("Changes and Challenges within the Business", "changes_and_challenges"),
            TableRow("Go-to-Market Approach", "go_to_market"),
            TableRow("Interactions with Public HCPs/GOs/HCIs", "interactions"),
            TableRow('Third-Party Intermediary ("TPI") Overview', "tpi_overview"),
            TableRow("Key Regulations", "key_regulations"),
        ),
        left=0.5, top=1.05, width=12.33, height=5.9, topic_col_width=2.7,
        header_font=_BODY_FONT, header_size=18, header_color_hex="FFFFFF", header_fill_hex="EB1700",
        topic_font=_BODY_FONT, topic_size=12, topic_color_hex="000000",
        content_font="Johnson Text", content_size=11, content_color_hex="000000",
    )


BUSINESS_OVERVIEW_SLIDE = SlideSpec(
    name="Business Overview",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="{entity} Business Overview",
        ),
        TableField(
            kind=FieldKind.TABLE,
            header_template="Business Summary – {entity}",
            rows=(
                TableRow("Business Overview", "business_overview"),
                TableRow("Changes and Challenges within the Business", "changes_and_challenges"),
                TableRow("Go-to-Market Approach", "go_to_market"),
                TableRow("Interactions with Public HCPs/GOs/HCIs", "interactions"),
                TableRow('Third-Party Intermediary ("TPI") Overview', "tpi_overview"),
                TableRow("Key Regulations", "key_regulations"),
            ),
            left=0.5, top=1.05, width=12.33, height=5.9, topic_col_width=2.7,
            header_font=_BODY_FONT, header_size=18, header_color_hex="FFFFFF", header_fill_hex="EB1700",
            topic_font=_BODY_FONT, topic_size=12, topic_color_hex="000000",
            content_font="Johnson Text", content_size=11, content_color_hex="000000",
        ),
    ),
)

# Sam_1 only: separate Business Overview slides for Australia and New Zealand.
def _business_table_keyed(header: str, key_suffix: str) -> TableField:
    """Business table whose content keys are suffixed (e.g. '_au') for per-country generation."""
    suffix = f"_{key_suffix}" if key_suffix else ""
    return TableField(
        kind=FieldKind.TABLE,
        header_template=header,
        rows=(
            TableRow("Business Overview", f"business_overview{suffix}"),
            TableRow("Changes and Challenges within the Business", f"changes_and_challenges{suffix}"),
            TableRow("Go-to-Market Approach", f"go_to_market{suffix}"),
            TableRow("Interactions with Public HCPs/GOs/HCIs", f"interactions{suffix}"),
            TableRow('Third-Party Intermediary ("TPI") Overview', f"tpi_overview{suffix}"),
            TableRow("Key Regulations", f"key_regulations{suffix}"),
        ),
        left=0.5, top=1.05, width=12.33, height=5.9, topic_col_width=2.7,
        header_font=_BODY_FONT, header_size=18, header_color_hex="FFFFFF", header_fill_hex="EB1700",
        topic_font=_BODY_FONT, topic_size=12, topic_color_hex="000000",
        content_font="Johnson Text", content_size=11, content_color_hex="000000",
    )


BUSINESS_OVERVIEW_AU_SLIDE = SlideSpec(
    name="Business Overview — Australia",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="IM Australia Business Overview",
        ),
        _business_table_keyed("Business Summary – IM Australia", "au"),
    ),
)

BUSINESS_OVERVIEW_NZ_SLIDE = SlideSpec(
    name="Business Overview — New Zealand",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="IM New Zealand Business Overview",
        ),
        _business_table_keyed("Business Summary – IM New Zealand", "nz"),
    ),
)

RISKS_NOTED_SLIDE = SlideSpec(
    name="Risks noted by Management",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="Risks noted by {audit_entity} Management",
        ),
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_BODY_FONT, font_size=16, color_hex=_BODY_COLOR, bold=True,
            left=0.5, top=1.1, width=12.3, height=0.5,
            text_template=(
                "{audit_entity} Management indicated the following risk areas to be "
                "considered in our review:"
            ),
        ),
        FieldSpec(
            kind=FieldKind.BODY,
            font_name="Johnson Text", font_size=14, color_hex=_BODY_COLOR, bold=False,
            left=0.5, top=1.7, width=12.3, height=5.25,
            content_key="risks_noted", bullets=True, align=Alignment.LEFT,
            skip_if_empty=True,
        ),
    ),
)


LEGAL_RISKS_SLIDE = SlideSpec(
    name="Risks noted by Legal Counsel",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="Risks noted by Legal Counsel",
        ),
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_BODY_FONT, font_size=16, color_hex=_BODY_COLOR, bold=True,
            left=0.5, top=1.1, width=12.3, height=0.5,
            text_template="The following risks were noted by Legal Counsel:",
        ),
        FieldSpec(
            kind=FieldKind.BODY,
            font_name="Johnson Text", font_size=14, color_hex=_BODY_COLOR, bold=False,
            left=0.5, top=1.7, width=12.3, height=5.25,
            content_key="legal_risks", bullets=True, align=Alignment.LEFT,
            skip_if_empty=True,
        ),
    ),
)

SI_SUMMARY_SLIDE = SlideSpec(
    name="Sensitive Issue (SI) Investigations Summary",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.9,
            text_template="Sensitive Issue (SI) Investigations Summary",
        ),
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_BODY_FONT, font_size=16, color_hex=_BODY_COLOR, bold=True,
            left=0.5, top=1.15, width=12.3, height=0.5,
            text_template="The following issues were noted in SI History:",
        ),
        FieldSpec(
            kind=FieldKind.BODY,
            font_name="Johnson Text", font_size=14, color_hex=_BODY_COLOR, bold=False,
            left=0.5, top=1.75, width=12.3, height=5.2,
            content_key="si_summary", bullets=True, align=Alignment.LEFT,
            skip_if_empty=True,
        ),
    ),
)


ADDITIONAL_INPUT_SLIDE = SlideSpec(
    name="Additional Inputs",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.9,
            text_template="Additional Inputs",
        ),
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_BODY_FONT, font_size=16, color_hex=_BODY_COLOR, bold=True,
            left=0.5, top=1.15, width=12.3, height=0.5,
            text_template="The following additional inputs were noted:",
        ),
        FieldSpec(
            kind=FieldKind.BODY,
            font_name="Johnson Text", font_size=14, color_hex=_BODY_COLOR, bold=False,
            left=0.5, top=1.75, width=12.3, height=5.2,
            content_key="additional_input", bullets=True, align=Alignment.LEFT,
            skip_if_empty=True,
        ),
    ),
)


def _prior_review_table(header_template: str, content_key: str) -> DynamicTableField:
    return DynamicTableField(
        kind=FieldKind.DYNAMIC_TABLE,
        header_template=header_template,
        content_key=content_key,
        left=0.5, top=1.05, width=12.33, height=5.65, topic_col_width=2.7,
        header_font=_BODY_FONT, header_size=18, header_color_hex="FFFFFF", header_fill_hex="EB1700",
        topic_font=_BODY_FONT, topic_size=12, topic_color_hex="000000",
        content_font="Johnson Text", content_size=11, content_color_hex="000000",
    )


def _prior_review_title(text_template: str) -> FieldSpec:
    return FieldSpec(
        kind=FieldKind.TITLE,
        font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
        left=0.4, top=0.2, width=12.9, height=0.8,
        text_template=text_template,
    )


PRIOR_CPR_SLIDE = SlideSpec(
    name="Prior Compliance Process Review Assessment",
    fields=(
        _prior_review_title("Prior Compliance Process Review Assessment"),
        _prior_review_table("Findings from previous CPR {cpr_year}", "cpr_findings"),
    ),
)

PRIOR_GACS_SLIDE = SlideSpec(
    name="Prior GACS Assessment",
    fields=(
        _prior_review_title("Prior GACS Assessment"),
        _prior_review_table("Findings from GACS Assessment in {gacs_year}", "gacs_findings"),
    ),
)

def _areas_table(headers: tuple[str, ...], content_key: str, col_widths: tuple[float, ...]) -> DataTableField:
    return DataTableField(
        kind=FieldKind.DATA_TABLE,
        headers=headers,
        content_key=content_key,
        col_widths=col_widths,
        left=0.5, top=1.05, width=12.33, height=5.9,
        header_font=_BODY_FONT, header_size=16, header_color_hex="FFFFFF", header_fill_hex="EB1700",
        first_col_font=_BODY_FONT, first_col_size=12, first_col_color_hex="000000",
        cell_font="Johnson Text", cell_size=11, cell_color_hex="000000",
        max_rows_per_slide=8,
    )


AREAS_IN_SCOPE_SLIDE = SlideSpec(
    name="Areas in Scope – Proposed",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="Areas in Scope – Proposed",
        ),
        _areas_table(
            headers=(
                "Compliance Process Review Area", "Full / Limited Scope", "Sample Size",
                "Total no. of transactions", "Total amount of transactions (Local / USD)", "Comments",
            ),
            content_key="scope_in_rows",
            col_widths=(2.6, 1.4, 1.1, 1.7, 1.9, 3.63),
        ),
    ),
)

AREAS_NOT_IN_SCOPE_SLIDE = SlideSpec(
    name="Areas in not Scope – Proposed",
    fields=(
        FieldSpec(
            kind=FieldKind.TITLE,
            font_name=_HEADER_FONT, font_size=44, color_hex=_HEADER_COLOR, bold=False,
            left=0.5, top=0.2, width=12.3, height=0.8,
            text_template="Areas in not Scope – Proposed",
        ),
        _areas_table(
            headers=(
                "Compliance Process Review Area", "Total no. of transactions",
                "Total amount of transactions (Local / USD)", "Comments",
            ),
            content_key="scope_out_rows",
            col_widths=(3.2, 2.4, 2.8, 3.93),
        ),
    ),
)


THANK_YOU_SLIDE = SlideSpec(
    name="Thank You",
    background_hex=_HEADER_COLOR,   # full red background
    footer="closing",              # single-line white logo only, no footer text / page number
    fields=(
        FieldSpec(
            kind=FieldKind.TEXT,
            font_name=_HEADER_FONT, font_size=70, color_hex="FFFFFF", bold=False,
            left=0.7, top=2.7, width=11.0, height=1.6,
            text_template="Thank you", align=Alignment.LEFT,
        ),
    ),
)

DECK_SPEC: tuple[SlideSpec, ...] = (
    OPENING_SLIDE,
    MARKET_OVERVIEW_SLIDE,
    BUSINESS_OVERVIEW_SLIDE,
    RISKS_NOTED_SLIDE,
    LEGAL_RISKS_SLIDE,
    SI_SUMMARY_SLIDE,
    PRIOR_CPR_SLIDE,
    PRIOR_GACS_SLIDE,
    AREAS_IN_SCOPE_SLIDE,
    AREAS_NOT_IN_SCOPE_SLIDE,
    THANK_YOU_SLIDE,
)

# Sam_1 only — two Market Overview slides (AU + NZ) replace the single slide.
SAM_1_DECK_SPEC: tuple[SlideSpec, ...] = (
    OPENING_SLIDE,
    MARKET_OVERVIEW_AU_SLIDE,
    MARKET_OVERVIEW_NZ_SLIDE,
    BUSINESS_OVERVIEW_AU_SLIDE,
    BUSINESS_OVERVIEW_NZ_SLIDE,
    RISKS_NOTED_SLIDE,
    LEGAL_RISKS_SLIDE,
    SI_SUMMARY_SLIDE,
    PRIOR_CPR_SLIDE,
    PRIOR_GACS_SLIDE,
    AREAS_IN_SCOPE_SLIDE,
    AREAS_NOT_IN_SCOPE_SLIDE,
    THANK_YOU_SLIDE,
)
