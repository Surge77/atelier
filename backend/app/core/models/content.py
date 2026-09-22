"""The content plan: the single source of truth for every artifact.

A DOCX or PPTX file is only ever a *rendering* of one of these. That is the
decision the rest of the system hangs off:

- Editing rewrites the plan and re-renders through the same template, so
  formatting cannot drift the way it does when a file is regenerated freehand.
- Converting a document to a deck is a transform between two plan shapes, not
  a file conversion.
- A version is a plan snapshot, so diffing two versions is diffing two plans.
- Citation ids live on blocks and slides, so attribution survives every edit.

Ids are short and stable (``sec1``, ``sld3``, ``b2``) because patch operations
target them by name and a model has to emit them reliably.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.core.models.template import LayoutRole

BlockKind = Literal["paragraph", "bullets", "numbered", "table", "quote"]


class Block(BaseModel):
    """One unit of body content inside a section."""

    id: str = ""
    kind: BlockKind = "paragraph"
    # paragraph and quote
    text: str = ""
    # bullets and numbered
    items: list[str] = Field(default_factory=list)
    # table; the first row is treated as a header
    rows: list[list[str]] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.text.strip() or self.items or self.rows)

    def word_count(self) -> int:
        parts = [self.text, *self.items, *(cell for row in self.rows for cell in row)]
        return sum(len(part.split()) for part in parts)


class Section(BaseModel):
    id: str = ""
    heading: str
    level: int = 1
    blocks: list[Block] = Field(default_factory=list)

    def citation_ids(self) -> list[str]:
        return [cid for block in self.blocks for cid in block.citation_ids]

    def word_count(self) -> int:
        return sum(block.word_count() for block in self.blocks)


class Slide(BaseModel):
    id: str = ""
    layout: LayoutRole = "title_content"
    title: str = ""
    bullets: list[str] = Field(default_factory=list)
    # Used by title and section-header slides, which have prose, not bullets.
    body_text: str = ""
    speaker_notes: str = ""
    citation_ids: list[str] = Field(default_factory=list)


class DocumentPlan(BaseModel):
    kind: Literal["document"] = "document"
    title: str
    subtitle: str | None = None
    sections: list[Section] = Field(default_factory=list)

    @model_validator(mode="after")
    def _assign_ids(self) -> DocumentPlan:
        _fill_ids(self.sections, "sec")
        for section in self.sections:
            _fill_ids(section.blocks, "b")
        return self

    def citation_ids(self) -> list[str]:
        return [cid for section in self.sections for cid in section.citation_ids()]

    def find_section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)

    def word_count(self) -> int:
        return sum(section.word_count() for section in self.sections)

    def outline(self) -> list[str]:
        return [section.heading for section in self.sections]


class DeckPlan(BaseModel):
    kind: Literal["deck"] = "deck"
    title: str
    subtitle: str | None = None
    slides: list[Slide] = Field(default_factory=list)

    @model_validator(mode="after")
    def _assign_ids(self) -> DeckPlan:
        _fill_ids(self.slides, "sld")
        return self

    def citation_ids(self) -> list[str]:
        return [cid for slide in self.slides for cid in slide.citation_ids]

    def find_slide(self, slide_id: str) -> Slide | None:
        return next((s for s in self.slides if s.id == slide_id), None)

    def outline(self) -> list[str]:
        return [slide.title for slide in self.slides]


ContentPlan = DocumentPlan | DeckPlan


def _fill_ids(items: list, prefix: str) -> None:
    """Give every item a stable id, keeping any the model already supplied.

    Mutates in place because this runs inside a Pydantic post-validator, which
    is the one place the model is legitimately still being constructed.
    """
    used = {item.id for item in items if item.id}
    counter = 1

    for item in items:
        if item.id:
            continue
        while f"{prefix}{counter}" in used:
            counter += 1
        item.id = f"{prefix}{counter}"
        used.add(item.id)


def plan_from_dict(data: dict) -> ContentPlan:
    """Rebuild a plan from its stored JSON, choosing the right model by kind."""
    if data.get("kind") == "deck":
        return DeckPlan.model_validate(data)
    return DocumentPlan.model_validate(data)
