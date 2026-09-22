"""PPTX reader.

Reads slides in order, separating the title from body bullets and keeping
outline depth, because a deck's rhythm — how many bullets a slide carries and
how long they are — is part of the style a generated deck has to match.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation

from backend.app.ingestion.base import ExtractedBlock, ExtractedDocument


def read_pptx(path: Path, display_name: str | None = None) -> ExtractedDocument:
    presentation = Presentation(str(path))
    blocks: list[ExtractedBlock] = []

    for number, slide in enumerate(presentation.slides, start=1):
        blocks.extend(_slide_blocks(slide, number))

    return ExtractedDocument(
        source_filename=display_name or path.name,
        kind="pptx",
        blocks=blocks,
        page_count=len(presentation.slides),
    )


def _slide_blocks(slide: Any, number: int) -> list[ExtractedBlock]:
    blocks: list[ExtractedBlock] = []
    layout_name = getattr(slide.slide_layout, "name", None)

    title_shape = slide.shapes.title
    if title_shape is not None and title_shape.has_text_frame:
        title = title_shape.text_frame.text.strip()
        if title:
            blocks.append(
                ExtractedBlock(
                    kind="slide_title", text=title, style_name=layout_name, location=number
                )
            )

    blocks.extend(_body_blocks(slide, title_shape, number))
    blocks.extend(_notes_blocks(slide, number))
    return blocks


def _body_blocks(slide: Any, title_shape: Any, number: int) -> list[ExtractedBlock]:
    """Every paragraph in every non-title text frame, keeping indent level."""
    blocks: list[ExtractedBlock] = []
    # python-pptx builds a fresh wrapper object on each access, so the title
    # must be identified by id rather than by object identity.
    title_id = title_shape.shape_id if title_shape is not None else None

    for shape in slide.shapes:
        if shape.shape_id == title_id or not shape.has_text_frame:
            continue

        for paragraph in shape.text_frame.paragraphs:
            text = "".join(run.text for run in paragraph.runs).strip()
            if text:
                blocks.append(
                    ExtractedBlock(
                        kind="bullet", text=text, level=paragraph.level or 0, location=number
                    )
                )

    return blocks


def _notes_blocks(slide: Any, number: int) -> list[ExtractedBlock]:
    if not slide.has_notes_slide:
        return []

    notes = slide.notes_slide.notes_text_frame
    text = notes.text.strip() if notes is not None else ""
    if not text:
        return []

    return [ExtractedBlock(kind="speaker_notes", text=text, location=number)]
