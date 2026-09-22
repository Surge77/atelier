"""Renders a ``DeckPlan`` to an editable PPTX.

Same principle as the DOCX renderer: the uploaded template *is* the output
file. Its existing slides are removed and new ones are added from the
template's own layouts, so the slide master, theme fonts, theme colours,
placeholder geometry and any background graphics are inherited rather than
recreated.

Deleting a slide has no public API in python-pptx. It takes two steps —
dropping the relationship from the presentation part and removing the entry
from ``sldIdLst`` — because removing only the list entry orphans the slide part
and leaves a file PowerPoint reports as corrupt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.presentation import Presentation as PptxPresentation
from pptx.util import Emu

from backend.app.core.logging import get_logger
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import DeckPlan, Slide
from backend.app.core.models.template import PptxTemplateProfile

logger = get_logger(__name__)

SOURCES_SLIDE_TITLE = "Sources"
# A sources slide crams badly beyond this; the rest continue on another slide.
MAX_SOURCES_PER_SLIDE = 8


def render_deck(
    plan: DeckPlan,
    profile: PptxTemplateProfile,
    destination: Path,
    template_path: Path | None = None,
    citations: list[Citation] | None = None,
) -> Path:
    """Write ``plan`` to ``destination``, styled like ``template_path``."""
    presentation = Presentation(str(template_path)) if template_path else Presentation()

    if template_path:
        _remove_all_slides(presentation)
        # Keep the template's own canvas size; a 4:3 template must not
        # silently become 16:9.
        presentation.slide_width = Emu(profile.slide_width_emu)
        presentation.slide_height = Emu(profile.slide_height_emu)

    for slide in plan.slides:
        _add_slide(presentation, slide, profile)

    _add_sources_slides(presentation, citations or [], profile)

    destination.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(destination))
    logger.info(
        "Rendered %s slides to %s (template: %s)",
        len(plan.slides),
        destination.name,
        template_path.name if template_path else "none",
    )
    return destination


def _remove_all_slides(presentation: PptxPresentation) -> None:
    """Empty the template of its example slides, keeping master and layouts."""
    slide_id_list = presentation.slides._sldIdLst
    part = presentation.part

    for slide_id in list(slide_id_list):
        relationship_id = slide_id.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        if relationship_id:
            # Without this the slide part survives with no reference to it.
            part.drop_rel(relationship_id)
        slide_id_list.remove(slide_id)


def _add_slide(presentation: PptxPresentation, slide: Slide, profile: PptxTemplateProfile) -> Any:
    layout_index = profile.layout_index_for(slide.layout)
    layout = presentation.slide_layouts[min(layout_index, len(presentation.slide_layouts) - 1)]
    rendered = presentation.slides.add_slide(layout)

    _set_title(rendered, slide.title)
    _fill_body(rendered, slide, profile)
    _set_notes(rendered, slide.speaker_notes)
    _remove_unfilled_placeholders(rendered)

    return rendered


def _set_title(rendered: Any, title: str) -> None:
    if not title.strip():
        return

    title_shape = rendered.shapes.title
    if title_shape is not None:
        title_shape.text_frame.text = title


def _fill_body(rendered: Any, slide: Slide, profile: PptxTemplateProfile) -> None:
    body = _body_placeholder(rendered)
    if body is None:
        return

    lines = slide.bullets or ([slide.body_text] if slide.body_text.strip() else [])
    lines = [line for line in lines if line.strip()][: profile.max_bullets_per_slide]
    if not lines:
        return

    frame = body.text_frame
    # The first paragraph already exists; writing to it rather than adding one
    # avoids a leading blank line.
    frame.text = lines[0]
    for line in lines[1:]:
        paragraph = frame.add_paragraph()
        paragraph.text = line
        paragraph.level = 0


def _body_placeholder(rendered: Any) -> Any:
    """The first non-title placeholder that can hold text."""
    title_id = rendered.shapes.title.shape_id if rendered.shapes.title is not None else None

    for placeholder in rendered.placeholders:
        if placeholder.shape_id != title_id and placeholder.has_text_frame:
            return placeholder
    return None


def _set_notes(rendered: Any, notes: str) -> None:
    if notes.strip():
        rendered.notes_slide.notes_text_frame.text = notes


def _remove_unfilled_placeholders(rendered: Any) -> None:
    """Drop placeholders left empty.

    An empty placeholder is invisible when presenting but shows its "Click to
    add text" prompt in the editor, which makes a generated deck look unfinished
    the moment the user opens it.
    """
    for placeholder in list(rendered.placeholders):
        if placeholder.has_text_frame and not placeholder.text_frame.text.strip():
            placeholder._element.getparent().remove(placeholder._element)


def _add_sources_slides(
    presentation: PptxPresentation,
    citations: list[Citation],
    profile: PptxTemplateProfile,
) -> None:
    """Append the Sources slides that make the deck's citations traceable."""
    if not citations:
        return

    for start in range(0, len(citations), MAX_SOURCES_PER_SLIDE):
        batch = citations[start : start + MAX_SOURCES_PER_SLIDE]
        continued = "" if start == 0 else " (continued)"
        _add_slide(
            presentation,
            Slide(
                layout="title_content",
                title=f"{SOURCES_SLIDE_TITLE}{continued}",
                bullets=[citation.reference_line() for citation in batch],
            ),
            # Sources are a reference list, not body content, so the template's
            # bullet budget must not truncate them.
            profile.model_copy(update={"max_bullets_per_slide": MAX_SOURCES_PER_SLIDE}),
        )
