"""Applies patch operations to a content plan.

Pure and immutable: the input plan is never mutated, a new plan is returned.
That is what makes versioning honest — the parent version's stored plan is
still exactly what was rendered for it — and it makes every operation trivial
to test.

An op targeting something that no longer exists is skipped with a warning
rather than raising. The model is working from a summary of the plan and will
occasionally name a stale id; losing one edit is a far better outcome than
failing the user's whole turn.
"""

from __future__ import annotations

from typing import TypeVar, cast

from backend.app.core.logging import get_logger
from backend.app.core.models.content import ContentPlan, DeckPlan, DocumentPlan, Section, Slide
from backend.app.core.models.patch import PatchOp

logger = get_logger(__name__)

PlanT = TypeVar("PlanT", bound=DocumentPlan | DeckPlan)


def apply_patch(plan: PlanT, ops: list[PatchOp]) -> tuple[PlanT, list[PatchOp]]:
    """Apply ``ops`` to a copy of ``plan``.

    Returns the new plan and the ops that actually took effect, so the version
    record describes what changed rather than what was attempted.
    """
    working = plan.model_copy(deep=True)
    applied: list[PatchOp] = []

    for op in ops:
        handler = _apply_deck_op if isinstance(working, DeckPlan) else _apply_document_op
        if handler(working, op):  # type: ignore[arg-type]
            applied.append(op)
        else:
            logger.warning("Skipped %s: target %r not found", op.op, op.target_id)

    # Re-validating reassigns ids to anything newly inserted without one.
    revalidated = cast(PlanT, type(working).model_validate(working.model_dump()))
    return revalidated, applied


def _apply_document_op(plan: DocumentPlan, op: PatchOp) -> bool:
    if op.op == "set_title":
        plan.title = op.title or plan.title
        return True

    if op.op == "insert_section":
        section = op.section or Section(heading=op.heading, level=op.level, blocks=op.blocks)
        _insert(plan.sections, section, op.position)
        return True

    index = _index_of(plan.sections, op.target_id)
    if index is None:
        return False

    if op.op == "replace_section":
        replacement = op.section or Section(
            heading=op.heading or plan.sections[index].heading,
            level=op.level,
            blocks=op.blocks,
        )
        # Keeping the id means later ops in the same batch still resolve.
        replacement.id = plan.sections[index].id
        plan.sections[index] = replacement
        return True

    if op.op == "update_section_heading":
        plan.sections[index].heading = op.heading or plan.sections[index].heading
        return True

    if op.op == "append_blocks":
        plan.sections[index].blocks.extend(op.blocks)
        return True

    if op.op == "delete_section":
        plan.sections.pop(index)
        return True

    if op.op == "move_section":
        _move(plan.sections, index, op.position)
        return True

    return False


def _apply_deck_op(plan: DeckPlan, op: PatchOp) -> bool:
    if op.op == "set_title":
        plan.title = op.title or plan.title
        return True

    if op.op == "insert_slide":
        slide = op.slide or Slide(title=op.title, bullets=op.bullets)
        _insert(plan.slides, slide, op.position)
        return True

    index = _index_of(plan.slides, op.target_id)
    if index is None:
        return False

    if op.op == "replace_slide":
        replacement = op.slide or Slide(
            title=op.title or plan.slides[index].title, bullets=op.bullets
        )
        replacement.id = plan.slides[index].id
        plan.slides[index] = replacement
        return True

    if op.op == "update_slide_title":
        plan.slides[index].title = op.title or plan.slides[index].title
        return True

    if op.op == "update_slide_bullets":
        plan.slides[index].bullets = list(op.bullets)
        return True

    if op.op == "delete_slide":
        plan.slides.pop(index)
        return True

    if op.op == "move_slide":
        _move(plan.slides, index, op.position)
        return True

    return False


def _index_of(items: list, target_id: str) -> int | None:
    return next((i for i, item in enumerate(items) if item.id == target_id), None)


def _insert(items: list, item, position: int | None) -> None:
    if position is None or position >= len(items):
        items.append(item)
    else:
        items.insert(max(position, 0), item)


def _move(items: list, index: int, position: int | None) -> None:
    item = items.pop(index)
    _insert(items, item, position)


def describe_plan(plan: ContentPlan) -> str:
    """A compact outline for the editing agent to target ops against.

    Sending the full plan would be expensive and would tempt the model into
    rewriting everything; the ids and headings are all it needs to decide what
    to change.
    """
    if isinstance(plan, DeckPlan):
        lines = [
            f"  {slide.id}: {slide.title!r} [{slide.layout}], "
            f"{len(slide.bullets)} bullet(s)"
            for slide in plan.slides
        ]
        return f"DECK {plan.title!r}\n" + "\n".join(lines)

    lines = [
        f"  {section.id}: {section.heading!r} (level {section.level}), "
        f"{len(section.blocks)} block(s), {section.word_count()} words"
        for section in plan.sections
    ]
    return f"DOCUMENT {plan.title!r}\n" + "\n".join(lines)
