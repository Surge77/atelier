"""Document to deck and back.

Both directions are pure transforms between two plan shapes — no file is read
or written here. That is the payoff of treating the plan as the source of
truth: "convert this proposal into a presentation" is a mapping, not a file
conversion, and it costs no model call.

Citation ids ride along with the content they belong to, so a converted
artifact keeps its source trail.
"""

from __future__ import annotations

import re

from backend.app.core.models.content import (
    Block,
    DeckPlan,
    DocumentPlan,
    Section,
    Slide,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Beyond this a slide stops being readable, so a long section spills over.
MAX_BULLETS_PER_SLIDE = 5
MAX_WORDS_PER_BULLET = 18
# A section shorter than this becomes one slide rather than getting a divider.
MIN_BLOCKS_FOR_SECTION_HEADER = 3


def document_to_deck(plan: DocumentPlan, max_bullets: int = MAX_BULLETS_PER_SLIDE) -> DeckPlan:
    """Turn a document into a deck: a title slide, then slides per section."""
    slides: list[Slide] = [
        Slide(
            layout="title",
            title=plan.title,
            body_text=plan.subtitle or "",
        )
    ]

    for section in plan.sections:
        slides.extend(_section_to_slides(section, max_bullets))

    return DeckPlan(title=plan.title, subtitle=plan.subtitle, slides=slides)


def _section_to_slides(section: Section, max_bullets: int) -> list[Slide]:
    points = [point for block in section.blocks for point in _block_to_points(block)]
    citation_ids = _unique(section.citation_ids())

    if not points:
        return [Slide(layout="section_header", title=section.heading)]

    batches = [points[i : i + max_bullets] for i in range(0, len(points), max_bullets)]
    slides: list[Slide] = []

    # A substantial section earns a divider; a short one would just add noise.
    if len(section.blocks) >= MIN_BLOCKS_FOR_SECTION_HEADER and len(batches) > 1:
        slides.append(Slide(layout="section_header", title=section.heading))

    for index, batch in enumerate(batches):
        suffix = "" if index == 0 else f" ({index + 1})"
        slides.append(
            Slide(
                layout="title_content",
                title=f"{section.heading}{suffix}",
                bullets=batch,
                speaker_notes=_notes_for(section),
                citation_ids=citation_ids,
            )
        )

    return slides


def _block_to_points(block: Block) -> list[str]:
    """Reduce a block to slide-length points."""
    if block.kind in ("bullets", "numbered"):
        return [_shorten(item) for item in block.items if item.strip()]

    if block.kind == "table":
        # The header row names the columns; the data rows become the points.
        return [" — ".join(str(cell) for cell in row) for row in block.rows[1:] if row]

    return [
        _shorten(sentence)
        for sentence in _SENTENCE_SPLIT.split(block.text)
        if sentence.strip()
    ]


def _shorten(text: str) -> str:
    words = text.strip().split()
    if len(words) <= MAX_WORDS_PER_BULLET:
        return text.strip()
    return " ".join(words[:MAX_WORDS_PER_BULLET]) + "…"


def _notes_for(section: Section) -> str:
    """Speaker notes from the section's own prose, so nothing is invented."""
    prose = [block.text.strip() for block in section.blocks if block.kind == "paragraph"]
    return " ".join(prose)[:600]


def deck_to_document(plan: DeckPlan) -> DocumentPlan:
    """Turn a deck into a document: one section per content slide.

    Title and section-header slides become headings rather than sections of
    their own, since they carry no body content.
    """
    sections: list[Section] = []

    for slide in plan.slides:
        if slide.layout == "title":
            continue

        blocks = _slide_to_blocks(slide)
        if not blocks and slide.layout == "section_header":
            sections.append(Section(heading=slide.title, level=1))
            continue

        sections.append(
            Section(
                heading=slide.title,
                level=2 if sections and sections[-1].level == 1 else 1,
                blocks=blocks,
            )
        )

    return DocumentPlan(title=plan.title, subtitle=plan.subtitle, sections=sections)


def _slide_to_blocks(slide: Slide) -> list[Block]:
    blocks: list[Block] = []

    # Speaker notes are the prose a presenter would say, which is exactly what
    # a document wants, so they lead the section.
    if slide.speaker_notes.strip():
        blocks.append(
            Block(kind="paragraph", text=slide.speaker_notes, citation_ids=slide.citation_ids)
        )

    if slide.bullets:
        blocks.append(
            Block(kind="bullets", items=list(slide.bullets), citation_ids=slide.citation_ids)
        )
    elif slide.body_text.strip():
        blocks.append(
            Block(kind="paragraph", text=slide.body_text, citation_ids=slide.citation_ids)
        )

    return blocks


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]
