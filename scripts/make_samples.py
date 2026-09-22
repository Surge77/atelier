"""Generate the sample corporate templates shipped in ``samples/templates``.

These stand in for the ``Company_Proposal.docx`` and ``Company_Template.pptx``
named in the assignment. They are generated rather than committed as opaque
binaries so that the styling being preserved is visible and reviewable: a
distinctive body font, a brand colour, custom heading styles, a running header
and footer, and renamed slide-master fonts.

Run:  python scripts/make_samples.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from docx.styles.style import ParagraphStyle
from pptx import Presentation
from pptx.dml.color import RGBColor as PptxColor
from pptx.presentation import Presentation as PptxPresentation
from pptx.util import Inches
from pptx.util import Pt as PptxPt

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "samples" / "templates"

BRAND_NAVY = RGBColor(0x1B, 0x36, 0x5D)
BRAND_TEAL = RGBColor(0x0F, 0x86, 0x8A)
BODY_FONT = "Georgia"
HEADING_FONT = "Trebuchet MS"

PPTX_NAVY = PptxColor(0x1B, 0x36, 0x5D)
PPTX_TEAL = PptxColor(0x0F, 0x86, 0x8A)


def build_proposal_docx(destination: Path) -> Path:
    """A styled proposal with the structure a real client document has."""
    document = Document()

    _style_docx(document)
    _add_header_and_footer(document)

    document.add_paragraph("Northwind Analytics", style="Title")
    subtitle = document.add_paragraph("Client Proposal — Confidential")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    document.add_paragraph("Executive Summary", style="Heading 1")
    document.add_paragraph(
        "Northwind Analytics proposes a phased engagement to modernise the "
        "client's reporting estate. The programme consolidates fragmented "
        "spreadsheets into a governed warehouse and delivers self-service "
        "analytics to three business units within two quarters."
    )

    document.add_paragraph("Scope of Work", style="Heading 1")
    document.add_paragraph(
        "The engagement is organised into three workstreams, each with its own "
        "acceptance criteria and exit review."
    )
    for item in (
        "Discovery and data source inventory",
        "Warehouse build and migration",
        "Self-service enablement and handover",
    ):
        document.add_paragraph(item, style="List Bullet")

    document.add_paragraph("Commercials", style="Heading 1")
    document.add_paragraph("Indicative fees by workstream are set out below.")

    table = document.add_table(rows=4, cols=3)
    table.style = "Light Grid Accent 1"
    _fill_table(
        table,
        [
            ["Workstream", "Duration", "Indicative Fee"],
            ["Discovery", "4 weeks", "GBP 48,000"],
            ["Warehouse Build", "10 weeks", "GBP 160,000"],
            ["Enablement", "6 weeks", "GBP 72,000"],
        ],
    )

    document.add_paragraph("Assumptions", style="Heading 2")
    document.add_paragraph(
        "Pricing assumes the client provides timely access to source systems "
        "and a nominated product owner for the duration of the engagement."
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(destination))
    return destination


def _style_docx(document: DocxDocument) -> None:
    """Give the template a distinctive, checkable identity."""
    normal = _paragraph_style(document, "Normal")
    normal.font.name = BODY_FONT
    normal.font.size = Pt(11)

    for name, font, size, colour in (
        ("Title", HEADING_FONT, 28, BRAND_NAVY),
        ("Heading 1", HEADING_FONT, 16, BRAND_NAVY),
        ("Heading 2", HEADING_FONT, 13, BRAND_TEAL),
        ("Heading 3", HEADING_FONT, 11, BRAND_TEAL),
    ):
        style = _paragraph_style(document, name)
        style.font.name = font
        style.font.size = Pt(size)
        style.font.color.rgb = colour
        style.font.bold = name != "Title"


def _paragraph_style(document: DocxDocument, name: str) -> ParagraphStyle:
    """python-docx types styles[...] as BaseStyle; only ParagraphStyle has .font."""
    return cast(ParagraphStyle, document.styles[name])


def _add_header_and_footer(document: DocxDocument) -> None:
    section = document.sections[0]
    section.start_type = WD_SECTION.NEW_PAGE

    header = section.header.paragraphs[0]
    header.text = "Northwind Analytics  |  Client Proposal"
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    footer = section.footer.paragraphs[0]
    footer.text = "Commercial in confidence"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _fill_table(table: Any, rows: list[list[str]]) -> None:
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            cell = table.cell(row_index, column_index)
            cell.text = value
            if row_index == 0:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.bold = True


def build_template_pptx(destination: Path) -> Path:
    """A deck template with branded master fonts and a worked example slide."""
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)

    _style_pptx_master(presentation)

    title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    _set_title(title_slide, "Northwind Analytics")
    _set_placeholder_text(title_slide, 1, "Capability Overview")

    content_slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    _set_title(content_slide, "How We Work")
    body = cast(Any, content_slide.placeholders[1]).text_frame
    body.text = "Discovery before build"
    for line in ("Governed data by default", "Enablement, not dependency"):
        paragraph = body.add_paragraph()
        paragraph.text = line
        paragraph.level = 0

    section_slide = presentation.slides.add_slide(presentation.slide_layouts[2])
    _set_title(section_slide, "Engagement Model")

    destination.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(destination))
    return destination


def _set_title(slide: Any, text: str) -> None:
    title = slide.shapes.title
    if title is not None:
        title.text = text


def _set_placeholder_text(slide: Any, idx: int, text: str) -> None:
    slide.placeholders[idx].text_frame.text = text


def _style_pptx_master(presentation: PptxPresentation) -> None:
    """Brand the slide master so inheritance is observable in output decks."""
    master = presentation.slide_master

    for placeholder in master.placeholders:
        text_frame = placeholder.text_frame
        for paragraph in text_frame.paragraphs:
            font = paragraph.font
            is_title = placeholder.placeholder_format.idx == 0
            font.name = HEADING_FONT if is_title else BODY_FONT
            font.size = PptxPt(36) if is_title else PptxPt(18)
            font.color.rgb = PPTX_NAVY if is_title else PPTX_TEAL


def main() -> None:
    proposal = build_proposal_docx(TEMPLATES_DIR / "Company_Proposal.docx")
    template = build_template_pptx(TEMPLATES_DIR / "Company_Template.pptx")
    print(f"wrote {proposal}")
    print(f"wrote {template}")


if __name__ == "__main__":
    main()
