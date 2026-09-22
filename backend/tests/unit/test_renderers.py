"""Renderers, verified by reopening what they wrote.

These are the tests that prove the assignment's hardest requirement — that a
generated file keeps the uploaded template's tone, structure, formatting and
visual style. Asserting on the plan would prove nothing; every assertion here
reads the produced .docx or .pptx back off disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from docx import Document
from pptx import Presentation

from backend.app.analysis.docx_profile import build_docx_profile
from backend.app.analysis.pptx_profile import build_pptx_profile
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import Block, DeckPlan, DocumentPlan, Section, Slide
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile
from backend.app.ingestion.docx_reader import read_docx
from backend.app.ingestion.pptx_reader import read_pptx
from backend.app.rendering.docx_renderer import render_document
from backend.app.rendering.pptx_renderer import render_deck

TEMPLATES = Path(__file__).resolve().parents[3] / "samples" / "templates"
PROPOSAL = TEMPLATES / "Company_Proposal.docx"
DECK = TEMPLATES / "Company_Template.pptx"

CITATIONS = [
    Citation(id="S1", kind="web", title="State of AI 2026", url="https://example.com/ai"),
    Citation(
        id="S2",
        kind="enterprise",
        title="Delivery Playbook",
        source_document="playbook.docx",
        locator="page 12",
    ),
]


def _style(path: Path, name: str) -> Any:
    """styles[...] is typed as BaseStyle; only ParagraphStyle exposes .font."""
    return cast(Any, Document(str(path)).styles[name])


def _require(path: Path) -> None:
    if not path.exists():
        pytest.skip("Run scripts/make_samples.py first.")


@pytest.fixture
def document_plan() -> DocumentPlan:
    return DocumentPlan(
        title="Generative AI Adoption Proposal",
        subtitle="Prepared for Northwind",
        sections=[
            Section(
                heading="Executive Summary",
                level=1,
                blocks=[
                    Block(kind="paragraph", text="Adoption has accelerated.", citation_ids=["S1"]),
                    Block(kind="bullets", items=["Faster drafting", "Lower review cost"]),
                ],
            ),
            Section(
                heading="Market Context",
                level=2,
                blocks=[
                    Block(
                        kind="table",
                        rows=[["Segment", "Growth"], ["Enterprise", "38%"]],
                        citation_ids=["S2"],
                    )
                ],
            ),
        ],
    )


@pytest.fixture
def deck_plan() -> DeckPlan:
    return DeckPlan(
        title="Generative AI Trends",
        slides=[
            Slide(layout="title", title="Generative AI Trends", body_text="Northwind Analytics"),
            Slide(
                layout="title_content",
                title="What Changed",
                bullets=["Model costs fell", "Tooling matured", "Governance caught up"],
                speaker_notes="Open with the cost curve.",
                citation_ids=["S1"],
            ),
            Slide(layout="section_header", title="Recommendations"),
        ],
    )


class TestDocxTemplatePreservation:
    @pytest.fixture
    def rendered(self, document_plan: DocumentPlan, tmp_path: Path) -> Path:
        _require(PROPOSAL)
        profile = build_docx_profile(PROPOSAL, read_docx(PROPOSAL))
        return render_document(
            document_plan, profile, tmp_path / "out.docx", PROPOSAL, CITATIONS
        )

    def test_produces_a_readable_docx(self, rendered: Path) -> None:
        assert rendered.exists()
        assert Document(str(rendered)) is not None

    def test_keeps_the_template_body_font(self, rendered: Path) -> None:
        assert _style(rendered, "Normal").font.name == "Georgia"

    def test_keeps_the_template_heading_colour(self, rendered: Path) -> None:
        assert str(_style(rendered, "Heading 1").font.color.rgb) == "1B365D"

    def test_keeps_the_running_header_and_footer(self, rendered: Path) -> None:
        section = Document(str(rendered)).sections[0]

        assert "Northwind Analytics" in section.header.paragraphs[0].text
        assert "confidence" in section.footer.paragraphs[0].text

    def test_keeps_the_page_geometry(self, rendered: Path) -> None:
        """Dropping sectPr would silently reset page size and margins."""
        original = Document(str(PROPOSAL)).sections[0]
        produced = Document(str(rendered)).sections[0]

        assert produced.page_width == original.page_width
        assert produced.left_margin == original.left_margin

    def test_drops_the_templates_own_content(self, rendered: Path) -> None:
        text = read_docx(rendered).text

        assert "Scope of Work" not in text  # a heading from the template
        assert "Executive Summary" in text  # a heading from the plan


class TestDocxContent:
    @pytest.fixture
    def rendered(self, document_plan: DocumentPlan, tmp_path: Path) -> Path:
        _require(PROPOSAL)
        profile = build_docx_profile(PROPOSAL, read_docx(PROPOSAL))
        return render_document(
            document_plan, profile, tmp_path / "out.docx", PROPOSAL, CITATIONS
        )

    def test_writes_headings_at_the_right_level(self, rendered: Path) -> None:
        headings = {b.text: b.style_name for b in read_docx(rendered).blocks_of("heading")}

        assert headings["Executive Summary"] == "Heading 1"
        assert headings["Market Context"] == "Heading 2"

    def test_writes_bullets_with_the_template_bullet_style(self, rendered: Path) -> None:
        bullets = read_docx(rendered).blocks_of("bullet")

        assert "Faster drafting" in [b.text for b in bullets]
        assert bullets[0].style_name == "List Bullet"

    def test_writes_the_table_with_its_rows_intact(self, rendered: Path) -> None:
        tables = read_docx(rendered).blocks_of("table")

        assert tables[0].rows[0] == ["Segment", "Growth"]
        assert tables[0].rows[1] == ["Enterprise", "38%"]

    def test_appends_a_references_section(self, rendered: Path) -> None:
        text = read_docx(rendered).text

        assert "References" in text
        assert "[S1] State of AI 2026 — https://example.com/ai" in text
        assert "[S2] Delivery Playbook (playbook.docx, page 12)" in text

    def test_omits_references_when_there_are_no_citations(
        self, document_plan: DocumentPlan, tmp_path: Path
    ) -> None:
        _require(PROPOSAL)
        profile = build_docx_profile(PROPOSAL, read_docx(PROPOSAL))

        out = render_document(document_plan, profile, tmp_path / "bare.docx", PROPOSAL)

        assert "References" not in read_docx(out).text


class TestDocxWithoutATemplate:
    def test_renders_against_the_default_template(
        self, document_plan: DocumentPlan, tmp_path: Path
    ) -> None:
        """A user with no template must still get a usable document."""
        profile = DocxTemplateProfile(
            source_filename="none",
            heading_styles={1: "Heading 1", 2: "Heading 2"},
            title_style="Title",
        )

        out = render_document(document_plan, profile, tmp_path / "plain.docx")

        assert "Executive Summary" in read_docx(out).text

    def test_a_style_missing_from_the_template_does_not_crash(
        self, document_plan: DocumentPlan, tmp_path: Path
    ) -> None:
        """The exact failure mode this design exists to prevent."""
        profile = DocxTemplateProfile(
            source_filename="none",
            heading_styles={1: "Corporate Heading That Does Not Exist"},
            bullet_style="Nonexistent Bullet Style",
        )

        out = render_document(document_plan, profile, tmp_path / "odd.docx")

        assert "Executive Summary" in read_docx(out).text


class TestPptxTemplatePreservation:
    @pytest.fixture
    def rendered(self, deck_plan: DeckPlan, tmp_path: Path) -> Path:
        _require(DECK)
        profile = build_pptx_profile(DECK, read_pptx(DECK))
        return render_deck(deck_plan, profile, tmp_path / "out.pptx", DECK, CITATIONS)

    def test_produces_a_readable_pptx(self, rendered: Path) -> None:
        assert Presentation(str(rendered)) is not None

    def test_keeps_the_template_slide_size(self, rendered: Path) -> None:
        original = Presentation(str(DECK))
        produced = Presentation(str(rendered))

        assert produced.slide_width == original.slide_width
        assert produced.slide_height == original.slide_height

    def test_keeps_the_template_layouts(self, rendered: Path) -> None:
        original = Presentation(str(DECK))
        produced = Presentation(str(rendered))

        assert len(produced.slide_layouts) == len(original.slide_layouts)

    def test_keeps_the_branded_master_font(self, rendered: Path) -> None:
        master = Presentation(str(rendered)).slide_master
        # placeholders[] is typed as BaseShape; a placeholder always has a frame.
        title_placeholder = cast(Any, master.placeholders[0])
        font = title_placeholder.text_frame.paragraphs[0].font

        assert font.name == "Trebuchet MS"
        assert str(font.color.rgb) == "1B365D"

    def test_drops_the_templates_own_slides(self, rendered: Path) -> None:
        titles = read_pptx(rendered).outline()

        assert "How We Work" not in titles  # from the template
        assert "What Changed" in titles  # from the plan

    def test_leaves_no_orphaned_slide_parts(self, rendered: Path) -> None:
        """Removing a sldIdLst entry without dropping its relationship
        produces a file PowerPoint reports as corrupt."""
        presentation = Presentation(str(rendered))
        slide_count = len(presentation.slides)
        related = [
            rel
            for rel in presentation.part.rels.values()
            if rel.reltype.endswith("/slide")
        ]

        assert len(related) == slide_count


class TestPptxContent:
    @pytest.fixture
    def rendered(self, deck_plan: DeckPlan, tmp_path: Path) -> Path:
        _require(DECK)
        profile = build_pptx_profile(DECK, read_pptx(DECK))
        return render_deck(deck_plan, profile, tmp_path / "out.pptx", DECK, CITATIONS)

    def test_writes_every_slide_plus_sources(self, rendered: Path) -> None:
        assert len(Presentation(str(rendered)).slides) == 4

    def test_writes_slide_titles_in_order(self, rendered: Path) -> None:
        assert read_pptx(rendered).outline() == [
            "Generative AI Trends",
            "What Changed",
            "Recommendations",
            "Sources",
        ]

    def test_writes_bullets(self, rendered: Path) -> None:
        bullets = [b.text for b in read_pptx(rendered).blocks_of("bullet")]

        assert "Model costs fell" in bullets
        assert "Governance caught up" in bullets

    def test_writes_speaker_notes(self, rendered: Path) -> None:
        notes = [b.text for b in read_pptx(rendered).blocks_of("speaker_notes")]

        assert "Open with the cost curve." in notes

    def test_appends_a_sources_slide(self, rendered: Path) -> None:
        bullets = [b.text for b in read_pptx(rendered).blocks_of("bullet")]

        assert any("State of AI 2026" in text for text in bullets)

    def test_respects_the_templates_bullet_budget(
        self, deck_plan: DeckPlan, tmp_path: Path
    ) -> None:
        _require(DECK)
        profile = build_pptx_profile(DECK, read_pptx(DECK)).model_copy(
            update={"max_bullets_per_slide": 2}
        )

        out = render_deck(deck_plan, profile, tmp_path / "tight.pptx", DECK)
        second_slide = [
            b for b in read_pptx(out).blocks_of("bullet") if b.location == 2
        ]

        assert len(second_slide) == 2

    def test_splits_a_long_source_list_across_slides(
        self, deck_plan: DeckPlan, tmp_path: Path
    ) -> None:
        _require(DECK)
        profile = build_pptx_profile(DECK, read_pptx(DECK))
        many = [
            Citation(id=f"S{i}", kind="web", title=f"Source {i}", url=f"https://e{i}.com")
            for i in range(1, 12)
        ]

        out = render_deck(deck_plan, profile, tmp_path / "many.pptx", DECK, many)
        titles = read_pptx(out).outline()

        assert titles.count("Sources") == 1
        assert "Sources (continued)" in titles


class TestPptxWithoutATemplate:
    def test_renders_against_the_default_template(
        self, deck_plan: DeckPlan, tmp_path: Path
    ) -> None:
        profile = PptxTemplateProfile(
            source_filename="none", layout_map={"title": 0, "title_content": 1}
        )

        out = render_deck(deck_plan, profile, tmp_path / "plain.pptx")

        assert "What Changed" in read_pptx(out).outline()

    def test_an_unknown_layout_role_falls_back(self, tmp_path: Path) -> None:
        profile = PptxTemplateProfile(source_filename="none")
        plan = DeckPlan(
            title="T", slides=[Slide(layout="two_content", title="Only Slide", bullets=["a"])]
        )

        out = render_deck(plan, profile, tmp_path / "fallback.pptx")

        assert "Only Slide" in read_pptx(out).outline()
