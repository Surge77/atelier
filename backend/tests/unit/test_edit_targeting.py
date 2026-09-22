"""Which artifact an unaddressed edit lands on.

Without inference, every follow-up edit hit whichever artifact was created
last — usually the deck — even when the user plainly meant the document.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.agents.supervisor import ExecutionPlan, describe_context
from backend.app.db.models import Artifact
from backend.app.orchestration.revisions import infer_target as _infer_target


def _artifact(kind: str, title: str, artifact_id: str) -> Artifact:
    return Artifact(id=artifact_id, conversation_id="c1", kind=kind, title=title)


@pytest.fixture
def state() -> SimpleNamespace:
    return SimpleNamespace(
        user_message="",
        existing_artifacts=[
            _artifact("document", "Proposal", "doc1"),
            _artifact("deck", "Deck", "deck1"),
        ],
    )


def _plan(instruction: str) -> ExecutionPlan:
    return ExecutionPlan(intent="edit", edit_instruction=instruction)


class TestInference:
    @pytest.mark.parametrize(
        "instruction",
        ["Make the presentation more concise.", "Add a slide on pricing.", "Fix the deck."],
    )
    def test_deck_wording_picks_the_deck(self, state, instruction: str) -> None:
        assert _infer_target(state, _plan(instruction)).kind == "deck"  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        "instruction",
        ["Add a competitive analysis section.", "Update the report.", "Reword that paragraph."],
    )
    def test_document_wording_picks_the_document(self, state, instruction: str) -> None:
        assert _infer_target(state, _plan(instruction)).kind == "document"  # type: ignore[union-attr]

    def test_ambiguous_wording_falls_back_to_the_newest(self, state) -> None:
        assert _infer_target(state, _plan("Add an executive summary.")).id == "deck1"  # type: ignore[union-attr]

    def test_mentioning_both_falls_back_to_the_newest(self, state) -> None:
        target = _infer_target(state, _plan("Update the report and the presentation."))
        assert target.id == "deck1"  # type: ignore[union-attr]

    def test_nothing_to_edit_returns_none(self) -> None:
        empty = SimpleNamespace(user_message="", existing_artifacts=[])

        assert _infer_target(empty, _plan("Anything.")) is None  # type: ignore[arg-type]

    def test_wording_with_no_matching_kind_still_returns_something(self) -> None:
        deck_only = SimpleNamespace(
            user_message="", existing_artifacts=[_artifact("deck", "Deck", "deck1")]
        )

        target = _infer_target(deck_only, _plan("Add a section."))  # type: ignore[arg-type]
        assert target.id == "deck1"  # type: ignore[union-attr]

    def test_the_user_message_is_considered_too(self, state) -> None:
        """The supervisor sometimes leaves edit_instruction empty."""
        state.user_message = "Make the slides shorter."

        assert _infer_target(state, _plan("")).kind == "deck"  # type: ignore[union-attr]


class TestSupervisorContext:
    def test_context_exposes_artifact_ids(self) -> None:
        described = describe_context([], ["doc1 - Proposal (document, v1)"])

        assert "doc1" in described
        assert "target_artifact_id" in described

    def test_empty_context_is_blank(self) -> None:
        assert describe_context([], []) == ""
