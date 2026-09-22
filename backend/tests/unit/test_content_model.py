"""The content plan: id assignment, lookup, and round-tripping."""

from __future__ import annotations

import pytest

from backend.app.core.models.content import (
    Block,
    DeckPlan,
    DocumentPlan,
    Section,
    Slide,
    plan_from_dict,
)


class TestIdAssignment:
    def test_sections_and_blocks_get_stable_ids(self) -> None:
        plan = DocumentPlan(
            title="T",
            sections=[
                Section(heading="One", blocks=[Block(text="a"), Block(text="b")]),
                Section(heading="Two", blocks=[Block(text="c")]),
            ],
        )

        assert [s.id for s in plan.sections] == ["sec1", "sec2"]
        assert [b.id for b in plan.sections[0].blocks] == ["b1", "b2"]

    def test_slides_get_stable_ids(self) -> None:
        plan = DeckPlan(title="T", slides=[Slide(title="A"), Slide(title="B")])

        assert [s.id for s in plan.slides] == ["sld1", "sld2"]

    def test_ids_supplied_by_the_model_are_kept(self) -> None:
        """A patch op targets an id, so regenerating them would break editing."""
        plan = DeckPlan(title="T", slides=[Slide(id="sld7", title="A"), Slide(title="B")])

        assert [s.id for s in plan.slides] == ["sld7", "sld1"]

    def test_generated_ids_never_collide_with_supplied_ones(self) -> None:
        plan = DeckPlan(
            title="T",
            slides=[Slide(id="sld1", title="A"), Slide(title="B"), Slide(title="C")],
        )

        ids = [s.id for s in plan.slides]

        assert len(set(ids)) == 3
        assert ids[0] == "sld1"

    def test_ids_survive_a_round_trip(self) -> None:
        plan = DocumentPlan(title="T", sections=[Section(heading="One")])

        restored = DocumentPlan.model_validate(plan.model_dump())

        assert restored.sections[0].id == plan.sections[0].id


class TestLookup:
    def test_finds_a_section_by_id(self) -> None:
        plan = DocumentPlan(title="T", sections=[Section(heading="Findings")])

        assert plan.find_section("sec1") is not None
        assert plan.find_section("sec1").heading == "Findings"  # type: ignore[union-attr]

    def test_missing_section_returns_none(self) -> None:
        assert DocumentPlan(title="T").find_section("sec9") is None

    def test_finds_a_slide_by_id(self) -> None:
        plan = DeckPlan(title="T", slides=[Slide(title="Agenda")])

        assert plan.find_slide("sld1").title == "Agenda"  # type: ignore[union-attr]


class TestCitations:
    def test_collects_ids_from_every_block(self) -> None:
        plan = DocumentPlan(
            title="T",
            sections=[
                Section(heading="A", blocks=[Block(text="x", citation_ids=["S1", "S2"])]),
                Section(heading="B", blocks=[Block(text="y", citation_ids=["S3"])]),
            ],
        )

        assert plan.citation_ids() == ["S1", "S2", "S3"]

    def test_collects_ids_from_every_slide(self) -> None:
        plan = DeckPlan(
            title="T",
            slides=[Slide(title="A", citation_ids=["S1"]), Slide(title="B", citation_ids=["S2"])],
        )

        assert plan.citation_ids() == ["S1", "S2"]

    def test_a_plan_with_no_citations_reports_none(self) -> None:
        assert DocumentPlan(title="T", sections=[Section(heading="A")]).citation_ids() == []


class TestBlocks:
    @pytest.mark.parametrize(
        "block",
        [
            Block(kind="paragraph", text="   "),
            Block(kind="bullets", items=[]),
            Block(kind="table", rows=[]),
        ],
    )
    def test_detects_an_empty_block(self, block: Block) -> None:
        assert block.is_empty()

    @pytest.mark.parametrize(
        "block",
        [
            Block(kind="paragraph", text="content"),
            Block(kind="bullets", items=["one"]),
            Block(kind="table", rows=[["a"]]),
        ],
    )
    def test_detects_a_non_empty_block(self, block: Block) -> None:
        assert not block.is_empty()

    def test_counts_words_across_every_field(self) -> None:
        assert Block(kind="paragraph", text="one two three").word_count() == 3
        assert Block(kind="bullets", items=["one two", "three"]).word_count() == 3
        assert Block(kind="table", rows=[["one", "two three"]]).word_count() == 3


class TestOutlineAndSize:
    def test_document_outline_lists_headings_in_order(self) -> None:
        plan = DocumentPlan(
            title="T", sections=[Section(heading="First"), Section(heading="Second")]
        )

        assert plan.outline() == ["First", "Second"]

    def test_deck_outline_lists_slide_titles(self) -> None:
        plan = DeckPlan(title="T", slides=[Slide(title="Agenda"), Slide(title="Detail")])

        assert plan.outline() == ["Agenda", "Detail"]

    def test_document_word_count_sums_every_block(self) -> None:
        plan = DocumentPlan(
            title="T",
            sections=[
                Section(heading="A", blocks=[Block(text="one two")]),
                Section(heading="B", blocks=[Block(text="three four five")]),
            ],
        )

        assert plan.word_count() == 5


class TestSerialisation:
    def test_rebuilds_a_document_plan_from_its_dict(self) -> None:
        plan = DocumentPlan(title="T", sections=[Section(heading="A", blocks=[Block(text="x")])])

        restored = plan_from_dict(plan.model_dump(mode="json"))

        assert isinstance(restored, DocumentPlan)
        assert restored == plan

    def test_rebuilds_a_deck_plan_from_its_dict(self) -> None:
        plan = DeckPlan(title="T", slides=[Slide(title="A", bullets=["one"])])

        restored = plan_from_dict(plan.model_dump(mode="json"))

        assert isinstance(restored, DeckPlan)
        assert restored == plan

    def test_defaults_to_a_document_when_kind_is_absent(self) -> None:
        assert isinstance(plan_from_dict({"title": "T"}), DocumentPlan)
