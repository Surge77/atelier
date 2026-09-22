"""Patch operations: how a conversational edit reaches an artifact.

An edit never touches the rendered file. The editing agent emits operations
against the *plan*, they are applied purely, and the result is re-rendered
through the same template. That is what makes "modify the existing artifact
without losing its structure and formatting" a structural guarantee rather
than something to hope for: the formatting was never in the plan to lose.

Operations are deliberately coarse — whole sections and slides, not character
ranges — because that is the level a user actually speaks at ("add a
competitive analysis section", "make the deck more concise").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.models.content import Block, Section, Slide

OpName = Literal[
    "set_title",
    "insert_section",
    "replace_section",
    "update_section_heading",
    "delete_section",
    "move_section",
    "append_blocks",
    "insert_slide",
    "replace_slide",
    "update_slide_title",
    "update_slide_bullets",
    "delete_slide",
    "move_slide",
]


class PatchOp(BaseModel):
    op: OpName

    # The section or slide this operates on. Required by every op except
    # set_title and the insert ops, which may append instead.
    target_id: str = ""
    # Where to insert or move to. Zero-based; omit to append at the end.
    position: int | None = None

    title: str = ""
    heading: str = ""
    level: int = 1

    blocks: list[Block] = Field(default_factory=list)
    slide: Slide | None = None
    section: Section | None = None
    bullets: list[str] = Field(default_factory=list)

    def describe(self) -> str:
        """One human-readable line, used in the trace and the version summary."""
        if self.op == "set_title":
            return f"retitled to {self.title!r}"
        if self.op in ("insert_section", "replace_section"):
            name = self.section.heading if self.section else self.heading
            verb = "added" if self.op == "insert_section" else "rewrote"
            return f"{verb} section {name!r}"
        if self.op in ("insert_slide", "replace_slide"):
            name = self.slide.title if self.slide else self.title
            verb = "added" if self.op == "insert_slide" else "rewrote"
            return f"{verb} slide {name!r}"
        if self.op in ("delete_section", "delete_slide"):
            return f"removed {self.target_id}"
        if self.op in ("move_section", "move_slide"):
            return f"moved {self.target_id} to position {self.position}"
        if self.op == "update_section_heading":
            return f"renamed {self.target_id} to {self.heading!r}"
        if self.op == "update_slide_title":
            return f"retitled {self.target_id} to {self.title!r}"
        if self.op == "update_slide_bullets":
            return f"rewrote the bullets on {self.target_id}"
        return f"{self.op} on {self.target_id}"


class PatchPlan(BaseModel):
    """What the editing agent returns."""

    ops: list[PatchOp] = Field(default_factory=list)
    explanation: str = Field(
        default="", description="One sentence telling the user what changed"
    )

    def is_empty(self) -> bool:
        return not self.ops

    def summary(self) -> str:
        return "; ".join(op.describe() for op in self.ops)
