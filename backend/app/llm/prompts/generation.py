"""Prompt construction for the generation agents.

Kept separate from the agents so the prompts can be read, diffed and tuned
without wading through orchestration, and so a prompt change shows up clearly
in review.

Three things do the heavy lifting here:

1. Verbatim excerpts from the uploaded template, not adjectives. Telling a
   model to write "formally" produces generic corporate prose; showing it four
   real sentences from the client's own document produces something that reads
   like the client.
2. The template's own outline, offered as structure to mirror.
3. Explicit citation rules. The model is told the exact ids available and that
   inventing one is a failure, because the validation agent checks this and a
   plan with unresolvable ids is rejected.
"""

from __future__ import annotations

from backend.app.core.models.citation import ResearchFindings
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile

DOCUMENT_SYSTEM_PROMPT = """\
You write business documents that match an existing house style exactly.

Rules:
- Mirror the supplied style: sentence length, voice, heading case, vocabulary.
- Every factual claim that came from the sources must carry the citation ids it
  came from, in that block's citation_ids. Use only ids that appear in the
  SOURCES section. Inventing an id is a failure.
- Claims you did not take from a source carry no citation ids. Do not attach an
  id to a sentence it does not support.
- Write substantive prose. Do not write placeholders, "TBD", or meta-commentary
  about the document itself.
- Use the requested heading levels: 1 for top-level sections, 2 for subsections.
"""

DECK_SYSTEM_PROMPT = """\
You build presentation decks that match an existing template's style exactly.

Rules:
- Bullets are phrases, not paragraphs. Respect the bullet budget given below.
- Every slide whose content came from the sources carries those citation ids in
  its citation_ids. Use only ids from the SOURCES section; inventing one is a
  failure.
- Give each content slide speaker notes: what the presenter should say, two or
  three sentences, not a repeat of the bullets.
- Produce exactly the number of slides requested, including the opening title
  slide and any section headers.
- Choose a layout per slide: "title" for the opener, "section_header" to divide
  parts, "title_content" for ordinary content, "two_content" to compare.
"""


def build_document_prompt(
    brief: str,
    profile: DocxTemplateProfile,
    findings: ResearchFindings,
) -> str:
    parts = [
        f"REQUEST\n{brief}",
        f"HOUSE STYLE TO MATCH\n{profile.tone.as_prompt_block()}",
    ]

    if profile.outline:
        parts.append(
            "STRUCTURE OF THE UPLOADED TEMPLATE\n"
            "Mirror this shape where it fits the request:\n"
            + "\n".join(f"  - {heading}" for heading in profile.outline[:20])
        )

    parts.append(_sources_block(findings))
    return "\n\n".join(parts)


def build_deck_prompt(
    brief: str,
    profile: PptxTemplateProfile,
    findings: ResearchFindings,
    slide_count: int,
) -> str:
    parts = [
        f"REQUEST\n{brief}",
        f"SLIDE COUNT\nProduce exactly {slide_count} slides.",
        (
            "DECK RHYTHM TO MATCH\n"
            f"At most {profile.max_bullets_per_slide} bullets per slide, "
            f"around {profile.avg_words_per_bullet:.0f} words per bullet."
        ),
        f"HOUSE STYLE TO MATCH\n{profile.tone.as_prompt_block()}",
    ]

    available = sorted(profile.layout_map)
    if available:
        parts.append("LAYOUTS AVAILABLE IN THIS TEMPLATE\n" + ", ".join(available))

    parts.append(_sources_block(findings))
    return "\n\n".join(parts)


def _sources_block(findings: ResearchFindings) -> str:
    """The evidence, plus the exact set of ids the model may cite."""
    if findings.is_empty():
        return (
            "SOURCES\n"
            "None were retrieved. Write from general knowledge, leave every "
            "citation_ids list empty, and do not invent sources."
        )

    ids = ", ".join(citation.id for citation in findings.citations)
    return (
        "SOURCES\n"
        f"Valid citation ids: {ids}\n"
        "Use only these ids.\n\n"
        f"{findings.as_prompt_block()}"
    )
