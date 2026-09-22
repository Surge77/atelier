"""Patch application: pure, immutable, and tolerant of stale ids."""

from __future__ import annotations

from backend.app.core.models.content import Block, DeckPlan, DocumentPlan, Section, Slide
from backend.app.core.models.patch import PatchOp
from backend.app.services.patching import apply_patch, describe_plan


def _document() -> DocumentPlan:
    return DocumentPlan(
        title="Proposal",
        sections=[
            Section(heading="Introduction", blocks=[Block(text="Opening remarks.")]),
            Section(heading="Approach", blocks=[Block(text="How we will work.")]),
            Section(heading="Pricing", blocks=[Block(text="Indicative fees.")]),
        ],
    )


def _deck() -> DeckPlan:
    return DeckPlan(
        title="Overview",
        slides=[
            Slide(title="Title Slide", layout="title"),
            Slide(title="Context", bullets=["One", "Two"]),
            Slide(title="Next Steps", bullets=["Sign off"]),
        ],
    )


class TestImmutability:
    def test_the_original_plan_is_never_mutated(self) -> None:
        """Versioning is only honest if the parent's plan still matches its file."""
        plan = _document()
        before = plan.model_dump()

        apply_patch(plan, [PatchOp(op="delete_section", target_id="sec1")])

        assert plan.model_dump() == before

    def test_returns_a_new_plan_object(self) -> None:
        plan = _document()
        updated, _ = apply_patch(plan, [PatchOp(op="set_title", title="New")])

        assert updated is not plan


class TestDocumentOps:
    def test_sets_the_title(self) -> None:
        updated, applied = apply_patch(_document(), [PatchOp(op="set_title", title="Revised")])

        assert updated.title == "Revised"
        assert len(applied) == 1

    def test_appends_a_section_by_default(self) -> None:
        op = PatchOp(
            op="insert_section",
            section=Section(heading="Competitive Analysis", blocks=[Block(text="Rivals.")]),
        )

        updated, _ = apply_patch(_document(), [op])

        assert updated.outline()[-1] == "Competitive Analysis"

    def test_inserts_a_section_at_a_position(self) -> None:
        op = PatchOp(op="insert_section", position=0, heading="Executive Summary")

        updated, _ = apply_patch(_document(), [op])

        assert updated.outline()[0] == "Executive Summary"

    def test_replaces_a_section_keeping_its_id(self) -> None:
        """Later ops in the same batch still have to resolve the id."""
        op = PatchOp(
            op="replace_section",
            target_id="sec2",
            section=Section(heading="Method", blocks=[Block(text="Revised approach.")]),
        )

        updated, _ = apply_patch(_document(), [op])

        assert updated.sections[1].id == "sec2"
        assert updated.sections[1].heading == "Method"

    def test_renames_a_section(self) -> None:
        op = PatchOp(op="update_section_heading", target_id="sec3", heading="Commercials")

        updated, _ = apply_patch(_document(), [op])

        assert updated.outline() == ["Introduction", "Approach", "Commercials"]

    def test_appends_blocks_to_a_section(self) -> None:
        op = PatchOp(op="append_blocks", target_id="sec1", blocks=[Block(text="More detail.")])

        updated, _ = apply_patch(_document(), [op])

        assert len(updated.sections[0].blocks) == 2

    def test_deletes_a_section(self) -> None:
        updated, _ = apply_patch(_document(), [PatchOp(op="delete_section", target_id="sec2")])

        assert updated.outline() == ["Introduction", "Pricing"]

    def test_moves_a_section(self) -> None:
        op = PatchOp(op="move_section", target_id="sec3", position=0)

        updated, _ = apply_patch(_document(), [op])

        assert updated.outline()[0] == "Pricing"


class TestDeckOps:
    def test_inserts_a_slide(self) -> None:
        op = PatchOp(
            op="insert_slide", position=1, slide=Slide(title="Agenda", bullets=["Scope"])
        )

        updated, _ = apply_patch(_deck(), [op])

        assert updated.outline()[1] == "Agenda"

    def test_rewrites_slide_bullets(self) -> None:
        op = PatchOp(op="update_slide_bullets", target_id="sld2", bullets=["Shorter"])

        updated, _ = apply_patch(_deck(), [op])

        assert updated.slides[1].bullets == ["Shorter"]

    def test_retitles_a_slide(self) -> None:
        op = PatchOp(op="update_slide_title", target_id="sld3", title="Recommendations")

        updated, _ = apply_patch(_deck(), [op])

        assert updated.outline()[-1] == "Recommendations"

    def test_deletes_a_slide(self) -> None:
        updated, _ = apply_patch(_deck(), [PatchOp(op="delete_slide", target_id="sld2")])

        assert len(updated.slides) == 2

    def test_moves_a_slide(self) -> None:
        op = PatchOp(op="move_slide", target_id="sld3", position=0)

        updated, _ = apply_patch(_deck(), [op])

        assert updated.outline()[0] == "Next Steps"


class TestResilience:
    def test_an_op_targeting_a_missing_id_is_skipped(self) -> None:
        """Losing one edit beats failing the user's whole turn."""
        ops = [
            PatchOp(op="delete_section", target_id="sec99"),
            PatchOp(op="set_title", title="Still Applied"),
        ]

        updated, applied = apply_patch(_document(), ops)

        assert len(applied) == 1
        assert updated.title == "Still Applied"

    def test_reports_only_the_ops_that_took_effect(self) -> None:
        ops = [PatchOp(op="delete_slide", target_id="nope")]

        _, applied = apply_patch(_deck(), ops)

        assert applied == []

    def test_a_document_op_on_a_deck_is_skipped(self) -> None:
        _, applied = apply_patch(_deck(), [PatchOp(op="delete_section", target_id="sec1")])

        assert applied == []

    def test_newly_inserted_items_receive_ids(self) -> None:
        op = PatchOp(op="insert_section", heading="Fresh", blocks=[Block(text="Body.")])

        updated, _ = apply_patch(_document(), [op])

        assert all(section.id for section in updated.sections)
        assert len({s.id for s in updated.sections}) == len(updated.sections)

    def test_several_ops_apply_in_order(self) -> None:
        ops = [
            PatchOp(op="delete_section", target_id="sec1"),
            PatchOp(op="update_section_heading", target_id="sec2", heading="Method"),
            PatchOp(op="set_title", title="Final"),
        ]

        updated, applied = apply_patch(_document(), ops)

        assert len(applied) == 3
        assert updated.title == "Final"
        assert updated.outline() == ["Method", "Pricing"]


class TestDescribePlan:
    def test_document_outline_carries_ids_and_sizes(self) -> None:
        described = describe_plan(_document())

        assert "sec1" in described
        assert "Introduction" in described
        assert "block(s)" in described

    def test_deck_outline_carries_ids_and_bullet_counts(self) -> None:
        described = describe_plan(_deck())

        assert "sld2" in described
        assert "bullet(s)" in described

    def test_describes_the_shape_not_the_full_text(self) -> None:
        """Sending the whole plan would tempt the model into rewriting it all."""
        assert "Opening remarks." not in describe_plan(_document())


class TestOpDescriptions:
    def test_each_op_describes_itself_for_the_version_summary(self) -> None:
        cases = [
            (PatchOp(op="set_title", title="X"), "retitled"),
            (PatchOp(op="insert_section", heading="Y"), "added section"),
            (PatchOp(op="delete_slide", target_id="sld1"), "removed"),
            (PatchOp(op="move_section", target_id="sec1", position=2), "moved"),
        ]

        for op, expected in cases:
            assert expected in op.describe()
