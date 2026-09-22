"""Builds a ``DocxTemplateProfile`` from an uploaded Word document.

Every style name in the profile is verified to exist in the file. This matters:
python-docx raises ``KeyError`` when asked to apply a style the document does
not define, and corporate templates routinely rename or delete the built-ins.
Guessing "Heading 1" would take the render down on exactly the documents this
feature exists to support.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import cast

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.style import WD_STYLE_TYPE
from docx.styles.style import ParagraphStyle

from backend.app.core.models.template import DocxTemplateProfile, ToneProfile
from backend.app.ingestion.base import ExtractedDocument

MAX_OUTLINE_ENTRIES = 40

# Checked in order; the first name present in the document wins.
_BULLET_CANDIDATES = ("List Bullet", "List Paragraph", "ListParagraph", "Body Text")
_NUMBER_CANDIDATES = ("List Number", "List Paragraph")
_QUOTE_CANDIDATES = ("Intense Quote", "Quote")
_TABLE_CANDIDATES = ("Light Grid Accent 1", "Table Grid", "Light List Accent 1")


def build_docx_profile(
    path: Path,
    extracted: ExtractedDocument,
    tone: ToneProfile | None = None,
) -> DocxTemplateProfile:
    document = Document(str(path))

    paragraph_styles = {
        style.name
        for style in document.styles
        if style.type == WD_STYLE_TYPE.PARAGRAPH and style.name
    }
    table_styles = {
        style.name
        for style in document.styles
        if style.type == WD_STYLE_TYPE.TABLE and style.name
    }

    # styles[...] is typed as BaseStyle; only ParagraphStyle exposes .font.
    normal = (
        cast(ParagraphStyle, document.styles["Normal"])
        if "Normal" in paragraph_styles
        else None
    )

    return DocxTemplateProfile(
        source_filename=extracted.source_filename,
        heading_styles=_heading_styles(extracted, paragraph_styles),
        title_style=_first_present(("Title",), paragraph_styles),
        body_style=_body_style(extracted, paragraph_styles),
        bullet_style=_bullet_style(extracted, paragraph_styles),
        numbered_style=_first_present(_NUMBER_CANDIDATES, paragraph_styles),
        quote_style=_first_present(_QUOTE_CANDIDATES, paragraph_styles),
        table_style=_table_style(extracted, table_styles),
        default_font=normal.font.name if normal is not None else None,
        default_font_size_pt=_font_size_pt(normal),
        has_header=_any_section_has(document, "header"),
        has_footer=_any_section_has(document, "footer"),
        outline=extracted.outline()[:MAX_OUTLINE_ENTRIES],
        tone=tone or ToneProfile(),
    )


def _heading_styles(extracted: ExtractedDocument, available: set[str]) -> dict[int, str]:
    """Prefer the heading styles the document actually uses, by level."""
    observed: dict[int, str] = {}

    for block in extracted.blocks_of("heading"):
        if block.style_name in available and block.level not in observed:
            observed[block.level] = block.style_name

    for level in range(1, 5):
        if level not in observed and (name := f"Heading {level}") in available:
            observed[level] = name

    return observed


def _body_style(extracted: ExtractedDocument, available: set[str]) -> str:
    """The most common paragraph style in the document, if it still exists."""
    counts = Counter(
        block.style_name
        for block in extracted.blocks_of("paragraph")
        if block.style_name in available
    )
    if counts:
        return counts.most_common(1)[0][0] or "Normal"
    return "Normal" if "Normal" in available else next(iter(sorted(available)), "Normal")


def _bullet_style(extracted: ExtractedDocument, available: set[str]) -> str:
    counts = Counter(
        block.style_name
        for block in extracted.blocks_of("bullet")
        if block.style_name in available
    )
    if counts:
        return counts.most_common(1)[0][0] or "List Bullet"
    return _first_present(_BULLET_CANDIDATES, available) or _body_style(extracted, available)


def _table_style(extracted: ExtractedDocument, available: set[str]) -> str | None:
    for block in extracted.blocks_of("table"):
        if block.style_name in available:
            return block.style_name
    return _first_present(_TABLE_CANDIDATES, available)


def _first_present(candidates: tuple[str, ...], available: set[str]) -> str | None:
    return next((name for name in candidates if name in available), None)


def _font_size_pt(style: ParagraphStyle | None) -> float | None:
    size = style.font.size if style is not None else None
    return float(size.pt) if size is not None else None


def _any_section_has(document: DocxDocument, part: str) -> bool:
    """True when any section defines non-empty header or footer text."""
    for section in document.sections:
        candidate = getattr(section, part, None)
        if candidate is None:
            continue
        if any(paragraph.text.strip() for paragraph in candidate.paragraphs):
            return True
    return False
