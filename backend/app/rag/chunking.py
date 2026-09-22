"""Splits an extracted document into retrievable chunks.

Chunking follows the document's own structure rather than a fixed character
window. A chunk that starts mid-sentence retrieves badly and cites worse —
"page 4" is a usable locator, "characters 3200-3800" is not. So blocks are
accumulated under the heading they sit beneath, and only a section longer than
the budget is split, at a sentence boundary.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from backend.app.ingestion.base import ExtractedBlock, ExtractedDocument

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Roughly four characters per token for English prose. Good enough to size a
# chunk; nothing downstream needs an exact token count.
CHARS_PER_TOKEN = 4


class Chunk(BaseModel):
    id: str
    text: str
    document_name: str
    # Heading the chunk sits under, used as its title in a citation.
    heading: str = ""
    # Page or slide number, used as the citation locator.
    location: int = 1
    metadata: dict[str, str] = Field(default_factory=dict)

    def locator_label(self, kind: str) -> str:
        return f"slide {self.location}" if kind == "pptx" else f"page {self.location}"


def chunk_document(
    extracted: ExtractedDocument,
    document_id: str,
    chunk_tokens: int = 320,
    overlap_tokens: int = 60,
) -> list[Chunk]:
    """Split a document into chunks aligned to its headings."""
    budget = chunk_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN

    chunks: list[Chunk] = []
    for section in _sections(extracted):
        chunks.extend(_split_section(section, extracted, document_id, len(chunks), budget, overlap))

    return chunks


class _Section(BaseModel):
    heading: str
    text: str
    location: int


def _sections(extracted: ExtractedDocument) -> list[_Section]:
    """Group blocks under the heading that precedes them."""
    sections: list[_Section] = []
    heading = ""
    buffer: list[str] = []
    location = 1

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(_Section(heading=heading, text=body, location=location))
        buffer.clear()

    for block in extracted.blocks:
        if block.kind in ("title", "heading", "slide_title"):
            flush()
            heading = block.text.strip()
            location = block.location
            continue

        if not buffer:
            location = block.location
        buffer.append(_block_text(block))

    flush()
    return sections


def _block_text(block: ExtractedBlock) -> str:
    if block.kind == "bullet":
        return f"- {block.text}"
    return block.text


def _split_section(
    section: _Section,
    extracted: ExtractedDocument,
    document_id: str,
    offset: int,
    budget: int,
    overlap: int,
) -> list[Chunk]:
    """One chunk per section, unless the section exceeds the budget."""
    body = f"{section.heading}\n{section.text}".strip() if section.heading else section.text

    pieces = [body] if len(body) <= budget else _split_on_sentences(body, budget, overlap)

    return [
        Chunk(
            id=f"{document_id}:{offset + index}",
            text=piece,
            document_name=extracted.source_filename,
            heading=section.heading,
            location=section.location,
            metadata={
                "document_id": document_id,
                "source_kind": extracted.kind,
                "heading": section.heading,
                "location": str(section.location),
                "document_name": extracted.source_filename,
            },
        )
        for index, piece in enumerate(pieces)
    ]


def _split_on_sentences(text: str, budget: int, overlap: int) -> list[str]:
    """Pack sentences up to the budget, carrying an overlap into the next chunk."""
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    pieces: list[str] = []
    current = ""

    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > budget:
            pieces.append(current.strip())
            current = f"{_tail(current, overlap)} {sentence}" if overlap else sentence
        else:
            current = f"{current} {sentence}".strip()

    if current.strip():
        pieces.append(current.strip())

    # A single sentence longer than the budget still has to go somewhere.
    return pieces or [text[:budget]]


def _tail(text: str, size: int) -> str:
    """Last ``size`` characters, trimmed to a word boundary."""
    if len(text) <= size:
        return text
    tail = text[-size:]
    _, _, remainder = tail.partition(" ")
    return remainder or tail
