"""Agent 9: conversational editing.

Turns "add a competitive analysis section" into patch operations against the
current plan. The agent sees an outline of the plan — ids, headings, sizes —
not its full text, which keeps the call cheap and discourages the model from
quietly rewriting sections the user did not mention.

Fresh research is threaded in the same way it is for generation, so "update the
report using the latest web information" works: the supervisor requests web
research, and the new sources arrive here as citable evidence.
"""

from __future__ import annotations

from backend.app.core.logging import get_logger
from backend.app.core.models.citation import ResearchFindings
from backend.app.core.models.content import Block, ContentPlan
from backend.app.core.models.patch import PatchOp, PatchPlan
from backend.app.core.models.template import ToneProfile
from backend.app.llm.base import LLMProvider, Message
from backend.app.services.patching import describe_plan

logger = get_logger(__name__)

EDITING_AGENT = "editing"

SYSTEM_PROMPT = """\
You edit an existing document or deck by emitting patch operations.

Rules:
- Change only what the user asked for. Every section or slide you do not
  mention is left exactly as it is.
- Target parts by the ids shown in the outline. Never invent an id.
- Operations available for a document: set_title, insert_section,
  replace_section, update_section_heading, append_blocks, delete_section,
  move_section.
- Operations available for a deck: set_title, insert_slide, replace_slide,
  update_slide_title, update_slide_bullets, delete_slide, move_slide.
- When inserting, supply the whole section or slide, written in the same voice
  as the rest of the artifact.
- "Make it more concise" means shortening existing content with replace or
  update operations, not deleting whole sections.
- Cite new factual claims with ids from SOURCES. Never invent an id.
- Write explanation as one sentence addressed to the user.
"""


def plan_edit(
    instruction: str,
    plan: ContentPlan,
    provider: LLMProvider,
    findings: ResearchFindings | None = None,
    tone: ToneProfile | None = None,
) -> PatchPlan:
    """Turn a natural-language instruction into patch operations."""
    resolved_findings = findings or ResearchFindings()

    parts = [
        f"CURRENT ARTIFACT\n{describe_plan(plan)}",
        f"USER INSTRUCTION\n{instruction}",
    ]
    if tone is not None:
        parts.append(f"VOICE TO MATCH\n{tone.as_prompt_block()}")
    if not resolved_findings.is_empty():
        ids = ", ".join(citation.id for citation in resolved_findings.citations)
        parts.append(
            f"SOURCES\nValid citation ids: {ids}\nUse only these.\n\n"
            f"{resolved_findings.as_prompt_block()}"
        )

    patch = provider.complete_structured(
        [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content="\n\n".join(parts)),
        ],
        PatchPlan,
    )

    patch = _drop_unknown_citations(patch, resolved_findings)
    logger.info("Edit produced %s operation(s)", len(patch.ops))
    return patch


def _drop_unknown_citations(patch: PatchPlan, findings: ResearchFindings) -> PatchPlan:
    """Remove citation ids the current research step did not produce."""
    known = {citation.id for citation in findings.citations}

    for op in patch.ops:
        if op.slide is not None:
            op.slide.citation_ids = [cid for cid in op.slide.citation_ids if cid in known]
        for block in _blocks_of(op):
            block.citation_ids = [cid for cid in block.citation_ids if cid in known]

    return patch


def _blocks_of(op: PatchOp) -> list[Block]:
    blocks = list(op.blocks)
    if op.section is not None:
        blocks.extend(op.section.blocks)
    return blocks
