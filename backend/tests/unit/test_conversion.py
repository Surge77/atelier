"""Document to deck and back.

Both directions are pure transforms between plan shapes, which is what makes
the assignment's "convert documents to presentations and vice versa" cost no
model call. These tests pin that the content and its citations survive.
"""

from __future__ import annotations

from backend.app.core.models.content import Block, DeckPlan, DocumentPlan, Section, Slide
from backend.app.services.conversion import deck_to_document, document_to_deck

PROSE = (
    "Adoption accelerated across every business unit. "
    "Costs fell by a third over the same period. "
    "Governance matured alongside the tooling."
)


def _document() -> DocumentPlan:
    return DocumentPlan(
        title="Adoption Proposal",
        subtitle="Prepared for Northwind",
        sections=[
            Section(
                heading="Executive Summary",
                blocks=[Block(kind="paragraph", text=PROSE, citation_ids=["S1"])],
            ),
            Section(
                heading="Findings",
                blocks=[
                    Block(kind="paragraph", text=PROSE),
                    Block(kind="bullets", items=["Faster drafting", "Lower review cost"]),
                    Block(
                        kind="table",
                        rows=[["Segment", "Growth"], ["Enterprise", "38%"], ["SMB", "12%"]],
                        citation_ids=["S2"],
                    ),
                ],
            ),
        ],
    )


def _deck() -> DeckPlan:
    return DeckPlan(
        title="Adoption Overview",
        subtitle="Northwind",
        slides=[
            Slide(layout="title", title="Adoption Overview", body_text="Northwind"),
            Slide(layout="section_header", title="Context"),
            Slide(
                layout="title_content",
                title="What Changed",
                bullets=["Costs fell", "Tooling matured"],
                speaker_notes="Open on the cost curve.",
                citation_ids=["S1"],
            ),
        ],
    )


class TestDocumentToDeck:
    def test_opens_with_a_title_slide(self) -> None:
        deck = document_to_deck(_document())

        assert deck.slides[0].layout == "title"
        assert deck.slides[0].title == "Adoption Proposal"
        assert deck.slides[0].body_text == "Prepared for Northwind"

    def test_keeps_the_document_title(self) -> None:
        assert document_to_deck(_document()).title == "Adoption Proposal"

    def test_every_section_reaches_a_slide(self) -> None:
        titles = " ".join(document_to_deck(_document()).outline())

        assert "Executive Summary" in titles
        assert "Findings" in titles

    def test_sentences_become_separate_bullets(self) -> None:
        deck = document_to_deck(_document())
        summary = next(s for s in deck.slides if s.title.startswith("Executive Summary"))

        assert len(summary.bullets) == 3

    def test_long_sentences_are_shortened(self) -> None:
        long_sentence = " ".join(["word"] * 60) + "."
        plan = DocumentPlan(
            title="T",
            sections=[Section(heading="A", blocks=[Block(text=long_sentence)])],
        )

        bullet = document_to_deck(plan).slides[1].bullets[0]

        assert len(bullet.split()) <= 19
        assert bullet.endswith("…")

    def test_table_rows_become_bullets_without_the_header(self) -> None:
        deck = document_to_deck(_document())
        bullets = [b for slide in deck.slides for b in slide.bullets]

        assert any("Enterprise" in b and "38%" in b for b in bullets)
        assert not any(b.startswith("Segment") for b in bullets)

    def test_citations_ride_along(self) -> None:
        deck = document_to_deck(_document())

        assert "S1" in deck.citation_ids()
        assert "S2" in deck.citation_ids()

    def test_speaker_notes_come_from_the_sections_own_prose(self) -> None:
        """Notes are taken from the document, never invented."""
        deck = document_to_deck(_document())
        summary = next(s for s in deck.slides if s.title.startswith("Executive Summary"))

        assert summary.speaker_notes.startswith("Adoption accelerated")

    def test_a_long_section_spills_onto_further_slides(self) -> None:
        many = Section(
            heading="Detail",
            blocks=[Block(kind="bullets", items=[f"Point {i}" for i in range(12)])],
        )
        deck = document_to_deck(DocumentPlan(title="T", sections=[many]), max_bullets=5)

        detail_slides = [s for s in deck.slides if s.title.startswith("Detail")]

        assert len(detail_slides) == 3

    def test_a_section_with_no_content_becomes_a_divider(self) -> None:
        plan = DocumentPlan(title="T", sections=[Section(heading="Appendix")])

        assert document_to_deck(plan).slides[1].layout == "section_header"

    def test_an_empty_document_still_yields_a_title_slide(self) -> None:
        assert len(document_to_deck(DocumentPlan(title="Empty")).slides) == 1


class TestDeckToDocument:
    def test_keeps_the_deck_title(self) -> None:
        assert deck_to_document(_deck()).title == "Adoption Overview"

    def test_the_title_slide_does_not_become_a_section(self) -> None:
        assert "Adoption Overview" not in deck_to_document(_deck()).outline()

    def test_a_section_header_slide_becomes_a_top_level_heading(self) -> None:
        document = deck_to_document(_deck())
        context = next(s for s in document.sections if s.heading == "Context")

        assert context.level == 1
        assert context.blocks == []

    def test_speaker_notes_lead_the_section_as_prose(self) -> None:
        """Notes are what a presenter would say, which is what a document wants."""
        document = deck_to_document(_deck())
        changed = next(s for s in document.sections if s.heading == "What Changed")

        assert changed.blocks[0].kind == "paragraph"
        assert changed.blocks[0].text == "Open on the cost curve."

    def test_bullets_survive_as_a_bullet_block(self) -> None:
        document = deck_to_document(_deck())
        changed = next(s for s in document.sections if s.heading == "What Changed")

        assert changed.blocks[1].items == ["Costs fell", "Tooling matured"]

    def test_citations_ride_along(self) -> None:
        assert "S1" in deck_to_document(_deck()).citation_ids()

    def test_a_slide_with_only_body_text_becomes_a_paragraph(self) -> None:
        deck = DeckPlan(
            title="T",
            slides=[Slide(layout="title_content", title="Note", body_text="Standalone prose.")],
        )

        section = deck_to_document(deck).sections[0]

        assert section.blocks[0].text == "Standalone prose."

    def test_an_empty_deck_yields_an_empty_document(self) -> None:
        assert deck_to_document(DeckPlan(title="Empty")).sections == []


class TestRoundTrip:
    def test_headings_survive_a_round_trip(self) -> None:
        restored = deck_to_document(document_to_deck(_document()))

        assert "Executive Summary" in " ".join(restored.outline())
        assert "Findings" in " ".join(restored.outline())

    def test_the_title_survives_a_round_trip(self) -> None:
        assert deck_to_document(document_to_deck(_document())).title == "Adoption Proposal"

    def test_citations_survive_a_round_trip(self) -> None:
        """The source trail is the thing that must not be lost in conversion."""
        restored = deck_to_document(document_to_deck(_document()))

        assert {"S1", "S2"} <= set(restored.citation_ids())

    def test_a_deck_survives_a_round_trip_back_to_a_deck(self) -> None:
        restored = document_to_deck(deck_to_document(_deck()))

        assert "What Changed" in " ".join(restored.outline())
