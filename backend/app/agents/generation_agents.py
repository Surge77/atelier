"""Agents 6 and 7: document generation and presentation generation.

Both take a brief, a template profile and research findings, and return a
content plan. Neither touches a file — rendering is a separate step — which is
what keeps generation testable and makes the same plan renderable as either
format.

Citation ids that the model invented are stripped here rather than left for the
validation agent. A hallucinated id would render into the References section as
a source that does not exist, which is worse than no citation at all.
"""

from __future__ import annotations

from typing import TypeVar

from backend.app.core.logging import get_logger
from backend.app.core.models.citation import ResearchFindings
from backend.app.core.models.content import DeckPlan, DocumentPlan
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile
from backend.app.llm.base import LLMProvider, Message
from backend.app.llm.prompts.generation import (
    DECK_SYSTEM_PROMPT,
    DOCUMENT_SYSTEM_PROMPT,
    build_deck_prompt,
    build_document_prompt,
)

logger = get_logger(__name__)

DOCUMENT_GENERATION_AGENT = "document_generation"
PPT_GENERATION_AGENT = "ppt_generation"

DEFAULT_SLIDE_COUNT = 10

PlanT = TypeVar("PlanT", DocumentPlan, DeckPlan)


def generate_document(
    brief: str,
    profile: DocxTemplateProfile,
    findings: ResearchFindings,
    provider: LLMProvider,
) -> DocumentPlan:
    """Agent 6. Produces a ``DocumentPlan`` in the template's voice."""
    plan = provider.complete_structured(
        [
            Message(role="system", content=DOCUMENT_SYSTEM_PROMPT),
            Message(role="user", content=build_document_prompt(brief, profile, findings)),
        ],
        DocumentPlan,
    )

    plan = _strip_unknown_citations(plan, findings)
    logger.info(
        "Generated %s sections, %s words", len(plan.sections), plan.word_count()
    )
    return plan


def generate_deck(
    brief: str,
    profile: PptxTemplateProfile,
    findings: ResearchFindings,
    provider: LLMProvider,
    slide_count: int = DEFAULT_SLIDE_COUNT,
) -> DeckPlan:
    """Agent 7. Produces a ``DeckPlan`` matching the template's rhythm."""
    plan = provider.complete_structured(
        [
            Message(role="system", content=DECK_SYSTEM_PROMPT),
            Message(
                role="user",
                content=build_deck_prompt(brief, profile, findings, slide_count),
            ),
        ],
        DeckPlan,
    )

    plan = _trim_bullets(plan, profile)
    plan = _strip_unknown_citations(plan, findings)
    logger.info("Generated %s slides", len(plan.slides))
    return plan


def _strip_unknown_citations(plan: PlanT, findings: ResearchFindings) -> PlanT:
    """Drop citation ids the research step never produced.

    Models occasionally emit a plausible-looking id such as ``S9`` when only
    S1-S4 exist. Rendering that would put a fabricated entry in the References
    section, so it is removed and logged rather than trusted.
    """
    known = {citation.id for citation in findings.citations}

    if isinstance(plan, DeckPlan):
        dropped = _clean_lists([slide.citation_ids for slide in plan.slides], known)
    else:
        dropped = _clean_lists(
            [block.citation_ids for section in plan.sections for block in section.blocks],
            known,
        )

    if dropped:
        logger.warning("Removed %s citation ids the sources do not contain", dropped)

    return plan


def _clean_lists(id_lists: list[list[str]], known: set[str]) -> int:
    """Filter each list in place, returning how many ids were removed."""
    removed = 0
    for ids in id_lists:
        kept = [cid for cid in ids if cid in known]
        removed += len(ids) - len(kept)
        ids[:] = kept
    return removed


def _trim_bullets(plan: DeckPlan, profile: PptxTemplateProfile) -> DeckPlan:
    """Enforce the template's bullet budget.

    Done here rather than in the renderer so that the stored plan matches what
    the file contains — otherwise an edit would operate on bullets the user
    never saw.
    """
    for slide in plan.slides:
        if len(slide.bullets) > profile.max_bullets_per_slide:
            slide.bullets = slide.bullets[: profile.max_bullets_per_slide]
    return plan
