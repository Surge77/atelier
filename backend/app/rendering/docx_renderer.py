"""Renders a ``DocumentPlan`` to an editable DOCX.

The whole trick is in ``_clear_body``. Rather than building a new document and
trying to re-create the template's look, the uploaded template *is* the output
file: it is opened, its body is emptied, and the new content is written back
using the style names the template itself defines.

Everything that makes a corporate document look the way it does — theme fonts
and colours, numbering definitions, headers and footers, page size and margins,
the style table itself — lives outside the body, so none of it is touched. It
is inherited rather than reconstructed, which is why it cannot drift.

The output is a real DOCX with real styles, so it is editable in Word exactly
as the assignment requires.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn

from backend.app.core.logging import get_logger
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import Block, DocumentPlan, Section
from backend.app.core.models.template import DocxTemplateProfile

logger = get_logger(__name__)

REFERENCES_HEADING = "References"


def render_document(
    plan: DocumentPlan,
    profile: DocxTemplateProfile,
    destination: Path,
    template_path: Path | None = None,
    citations: list[Citation] | None = None,
) -> Path:
    """Write ``plan`` to ``destination``, styled like ``template_path``."""
    document = Document(str(template_path)) if template_path else Document()

    if template_path:
        _clear_body(document)

    available = _available_styles(document)

    _write_title(document, plan, profile, available)
    for section in plan.sections:
        _write_section(document, section, profile, available)

    _write_references(document, citations or [], profile, available)

    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(destination))
    logger.info(
        "Rendered %s sections to %s (template: %s)",
        len(plan.sections),
        destination.name,
        template_path.name if template_path else "none",
    )
    return destination


def _clear_body(document: DocxDocument) -> None:
    """Remove the template's content, keeping everything that defines its look.

    ``w:sectPr`` is kept deliberately: it holds page size, margins, orientation
    and the links to the header and footer parts. Deleting it would silently
    reset the page geometry and drop the running header.
    """
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def _available_styles(document: DocxDocument) -> set[str]:
    """Style names this document actually defines.

    python-docx raises ``KeyError`` for an unknown style, which would abort the
    render. Every style name is checked against this set before use.
    """
    return {style.name for style in document.styles if style.name}


def _write_title(
    document: DocxDocument,
    plan: DocumentPlan,
    profile: DocxTemplateProfile,
    available: set[str],
) -> None:
    _add_paragraph(document, plan.title, profile.title_style, available, profile)

    if plan.subtitle:
        _add_paragraph(document, plan.subtitle, profile.body_style, available, profile)


def _write_section(
    document: DocxDocument,
    section: Section,
    profile: DocxTemplateProfile,
    available: set[str],
) -> None:
    _add_paragraph(
        document,
        section.heading,
        profile.heading_style_for(section.level),
        available,
        profile,
    )

    for block in section.blocks:
        _write_block(document, block, profile, available)


def _write_block(
    document: DocxDocument,
    block: Block,
    profile: DocxTemplateProfile,
    available: set[str],
) -> None:
    if block.is_empty():
        return

    if block.kind == "bullets":
        _add_list(document, block.items, profile.bullet_style, available, profile)
    elif block.kind == "numbered":
        style = profile.numbered_style or profile.bullet_style
        _add_list(document, block.items, style, available, profile)
    elif block.kind == "table":
        _add_table(document, block, profile, available)
    elif block.kind == "quote":
        style = profile.quote_style or profile.body_style
        _add_paragraph(document, block.text, style, available, profile)
    else:
        _add_paragraph(document, block.text, profile.body_style, available, profile)


def _add_list(
    document: DocxDocument,
    items: list[str],
    style: str | None,
    available: set[str],
    profile: DocxTemplateProfile,
) -> None:
    for item in items:
        if item.strip():
            _add_paragraph(document, item, style, available, profile)


def _add_table(
    document: DocxDocument,
    block: Block,
    profile: DocxTemplateProfile,
    available: set[str],
) -> None:
    rows = [row for row in block.rows if row]
    if not rows:
        return

    table = document.add_table(rows=len(rows), cols=max(len(row) for row in rows))

    if profile.table_style and profile.table_style in available:
        table.style = profile.table_style

    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            table.cell(row_index, column_index).text = str(value)

    # A header row is a convention of the content, not of the table style.
    for cell in table.rows[0].cells:
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.font.bold = True


def _write_references(
    document: DocxDocument,
    citations: list[Citation],
    profile: DocxTemplateProfile,
    available: set[str],
) -> None:
    """Append the References section that makes citations traceable on the page."""
    if not citations:
        return

    _add_paragraph(document, REFERENCES_HEADING, profile.heading_style_for(1), available, profile)
    for citation in citations:
        _add_paragraph(document, citation.reference_line(), profile.body_style, available, profile)


def _add_paragraph(
    document: DocxDocument,
    text: str,
    style: str | None,
    available: set[str],
    profile: DocxTemplateProfile,
) -> None:
    """Add a paragraph, degrading to the body style if the style is missing."""
    if not text.strip():
        return

    resolved = style if style and style in available else None
    if style and resolved is None:
        logger.debug("Style %r absent from the template; using the body style.", style)
        resolved = profile.body_style if profile.body_style in available else None

    document.add_paragraph(text, style=resolved)
