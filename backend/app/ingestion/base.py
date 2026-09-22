"""What every reader produces.

Readers return structure, not just text. Knowing that a line was a Heading 2
rather than a paragraph is what lets the analyser recover a template's outline
and the renderer reproduce its shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["docx", "pptx", "pdf", "image", "text"]

BlockKind = Literal[
    "title",
    "heading",
    "paragraph",
    "bullet",
    "table",
    "slide_title",
    "speaker_notes",
    "caption",
]


class ExtractedBlock(BaseModel):
    kind: BlockKind
    text: str
    # Heading depth, or bullet indent depth. 0 for body text.
    level: int = 0
    # The style the block actually carried, when the format records one.
    style_name: str | None = None
    # 1-based page number (PDF) or slide number (PPTX).
    location: int = 1
    rows: list[list[str]] = Field(default_factory=list)


class ExtractedDocument(BaseModel):
    source_filename: str
    kind: SourceKind
    blocks: list[ExtractedBlock] = Field(default_factory=list)
    page_count: int = 1
    # True when text came from a vision model rather than the file itself.
    used_ocr: bool = False

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.text.strip())

    def blocks_of(self, *kinds: BlockKind) -> list[ExtractedBlock]:
        return [block for block in self.blocks if block.kind in kinds]

    def outline(self) -> list[str]:
        """Heading and slide-title text in document order."""
        return [
            block.text.strip()
            for block in self.blocks
            if block.kind in ("title", "heading", "slide_title") and block.text.strip()
        ]
