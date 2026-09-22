"""DOCX reader.

``Document.paragraphs`` and ``Document.tables`` are separate sequences, so
reading them in turn loses the interleaving of prose and tables. This walks the
body XML instead, which preserves true document order — the thing the analyser
needs to recover an outline.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from backend.app.ingestion.base import BlockKind, ExtractedBlock, ExtractedDocument

# Style-name prefixes that identify a bulleted or numbered paragraph. Corporate
# templates rename these, so the check is a prefix match, not equality.
_LIST_STYLE_MARKERS = ("list bullet", "list number", "list paragraph", "bullet")


def read_docx(path: Path, display_name: str | None = None) -> ExtractedDocument:
    document = Document(str(path))
    blocks = [block for element in _iter_body(document) for block in _to_blocks(element)]

    return ExtractedDocument(
        source_filename=display_name or path.name,
        kind="docx",
        blocks=blocks,
        page_count=1,  # DOCX has no fixed pagination until it is laid out.
    )


def _iter_body(document: DocxDocument) -> list[Paragraph | Table]:
    """Yield paragraphs and tables in the order they appear in the body."""
    body = document.element.body
    items: list[Paragraph | Table] = []

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            items.append(Paragraph(child, document))
        elif child.tag == qn("w:tbl"):
            items.append(Table(child, document))

    return items


def _to_blocks(element: Paragraph | Table) -> list[ExtractedBlock]:
    if isinstance(element, Table):
        return [_table_block(element)]

    text = element.text.strip()
    if not text:
        return []

    style_name = element.style.name if element.style is not None else None
    kind, level = _classify(style_name, element)

    return [ExtractedBlock(kind=kind, text=text, level=level, style_name=style_name)]


def _classify(style_name: str | None, paragraph: Paragraph) -> tuple[BlockKind, int]:
    """Map a paragraph's style to a block kind and depth."""
    lowered = (style_name or "").lower()

    if lowered == "title":
        return "title", 0

    if lowered.startswith("heading"):
        return "heading", _heading_level(lowered)

    if any(lowered.startswith(marker) for marker in _LIST_STYLE_MARKERS):
        return "bullet", _list_indent(paragraph)

    # Numbering can also be applied directly, with no list style attached.
    if _has_direct_numbering(paragraph):
        return "bullet", _list_indent(paragraph)

    return "paragraph", 0


def _heading_level(lowered_style: str) -> int:
    trailing = lowered_style.replace("heading", "").strip()
    return int(trailing) if trailing.isdigit() else 1


def _has_direct_numbering(paragraph: Paragraph) -> bool:
    numbering = paragraph._p.find(qn("w:pPr"))
    return numbering is not None and numbering.find(qn("w:numPr")) is not None


def _list_indent(paragraph: Paragraph) -> int:
    """Indent depth of a list item, from its numbering level when present."""
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return 0

    numbering = properties.find(qn("w:numPr"))
    if numbering is not None:
        level = numbering.find(qn("w:ilvl"))
        if level is not None:
            return int(level.get(qn("w:val")) or 0)

    return 0


def _table_block(table: Table) -> ExtractedBlock:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    style_name = table.style.name if table.style is not None else None
    # A readable flattening for embedding and tone analysis; `rows` keeps the
    # structure for anything that needs the real grid.
    text = "\n".join(" | ".join(row) for row in rows)

    return ExtractedBlock(kind="table", text=text, rows=rows, style_name=style_name)
