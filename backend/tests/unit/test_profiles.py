"""Template and tone profiling, against the real sample templates."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from backend.app.analysis.docx_profile import build_docx_profile
from backend.app.analysis.pptx_profile import build_pptx_profile
from backend.app.analysis.tone_profile import build_tone_profile
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile
from backend.app.ingestion.base import ExtractedBlock, ExtractedDocument
from backend.app.ingestion.docx_reader import read_docx
from backend.app.ingestion.pptx_reader import read_pptx

TEMPLATES = Path(__file__).resolve().parents[3] / "samples" / "templates"
PROPOSAL = TEMPLATES / "Company_Proposal.docx"
DECK = TEMPLATES / "Company_Template.pptx"


def _require(path: Path) -> None:
    if not path.exists():
        pytest.skip("Run scripts/make_samples.py first.")


@pytest.fixture(scope="module")
def docx_profile() -> DocxTemplateProfile:
    _require(PROPOSAL)
    return build_docx_profile(PROPOSAL, read_docx(PROPOSAL))


@pytest.fixture(scope="module")
def pptx_profile() -> PptxTemplateProfile:
    _require(DECK)
    return build_pptx_profile(DECK, read_pptx(DECK))


class TestDocxProfile:
    def test_records_the_source_filename(self, docx_profile) -> None:
        assert docx_profile.source_filename == "Company_Proposal.docx"

    def test_maps_the_heading_levels_the_document_uses(self, docx_profile) -> None:
        assert docx_profile.heading_styles[1] == "Heading 1"
        assert docx_profile.heading_styles[2] == "Heading 2"

    def test_finds_the_bullet_style(self, docx_profile) -> None:
        assert docx_profile.bullet_style == "List Bullet"

    def test_reads_the_default_font_from_the_normal_style(self, docx_profile) -> None:
        assert docx_profile.default_font == "Georgia"
        assert docx_profile.default_font_size_pt == 11.0

    def test_detects_the_header_and_footer(self, docx_profile) -> None:
        assert docx_profile.has_header is True
        assert docx_profile.has_footer is True

    def test_captures_the_outline(self, docx_profile) -> None:
        assert "Executive Summary" in docx_profile.outline

    def test_finds_the_table_style_actually_used(self, docx_profile) -> None:
        assert docx_profile.table_style == "Light Grid Accent 1"

    def test_only_reports_styles_the_document_defines(self, tmp_path: Path) -> None:
        """A style name that is absent must never reach the renderer."""
        path = tmp_path / "plain.docx"
        Document().save(str(path))

        profile = build_docx_profile(path, read_docx(path))
        available = {s.name for s in Document(str(path)).styles}

        assert profile.body_style in available
        assert profile.bullet_style in available
        assert all(name in available for name in profile.heading_styles.values())


class TestHeadingFallback:
    def test_falls_back_to_a_nearby_level(self) -> None:
        profile = DocxTemplateProfile(
            source_filename="x.docx", heading_styles={1: "Heading 1", 2: "Heading 2"}
        )
        assert profile.heading_style_for(3) == "Heading 2"

    def test_falls_back_to_body_when_no_headings_exist(self) -> None:
        profile = DocxTemplateProfile(source_filename="x.docx", body_style="Normal")
        assert profile.heading_style_for(1) == "Normal"


class TestPptxProfile:
    def test_reads_the_slide_size(self, pptx_profile) -> None:
        assert pptx_profile.aspect_ratio == "16:9"
        assert pptx_profile.slide_width_emu > pptx_profile.slide_height_emu

    def test_describes_every_layout(self, pptx_profile) -> None:
        assert len(pptx_profile.layouts) >= 6
        assert all(layout.name for layout in pptx_profile.layouts)

    def test_maps_the_core_roles(self, pptx_profile) -> None:
        for role in ("title", "title_content", "section_header", "two_content", "blank"):
            assert role in pptx_profile.layout_map, role

    def test_title_role_points_at_the_title_slide_layout(self, pptx_profile) -> None:
        layout = pptx_profile.layouts[pptx_profile.layout_map["title"]]
        assert "title" in layout.name.lower()

    def test_two_content_layout_has_two_body_placeholders(self, pptx_profile) -> None:
        layout = pptx_profile.layouts[pptx_profile.layout_map["two_content"]]
        assert len(layout.extra_body_placeholder_idxs) >= 1

    def test_reads_the_theme_fonts(self, pptx_profile) -> None:
        assert pptx_profile.theme_major_font
        assert pptx_profile.theme_minor_font

    def test_measures_the_decks_bullet_rhythm(self, pptx_profile) -> None:
        assert pptx_profile.avg_bullets_per_slide > 0
        assert pptx_profile.avg_words_per_bullet > 0

    def test_unknown_role_degrades_to_a_real_layout(self, pptx_profile) -> None:
        assert pptx_profile.layout_index_for("does_not_exist") in range(
            len(pptx_profile.layouts)
        )


def _doc(*blocks: ExtractedBlock) -> ExtractedDocument:
    return ExtractedDocument(source_filename="t.docx", kind="docx", blocks=list(blocks))


def _para(text: str) -> ExtractedBlock:
    return ExtractedBlock(kind="paragraph", text=text)


class TestToneProfile:
    def test_detects_first_person_plural(self) -> None:
        document = _doc(
            _para("We deliver outcomes. Our team works closely with you."),
            _para("We have built this capability over many years of practice."),
        )
        assert build_tone_profile(document).person == "first person plural"

    def test_defaults_to_third_person(self) -> None:
        document = _doc(_para("The system processes records nightly and writes a report."))
        assert build_tone_profile(document).person == "third person"

    def test_detects_upper_case_headings(self) -> None:
        document = _doc(
            ExtractedBlock(kind="heading", text="EXECUTIVE SUMMARY", level=1),
            ExtractedBlock(kind="heading", text="SCOPE OF WORK", level=1),
        )
        assert build_tone_profile(document).heading_case == "UPPER CASE"

    def test_detects_sentence_case_headings(self) -> None:
        document = _doc(
            ExtractedBlock(kind="heading", text="Executive summary", level=1),
            ExtractedBlock(kind="heading", text="Scope of work", level=1),
        )
        assert build_tone_profile(document).heading_case == "Sentence case"

    def test_measures_sentence_length(self) -> None:
        document = _doc(_para("One two three four five. Six seven eight nine ten."))
        assert build_tone_profile(document).avg_sentence_words == 5.0

    def test_describes_bullets_that_end_in_a_full_stop(self) -> None:
        document = _doc(
            ExtractedBlock(kind="bullet", text="A complete thought stated plainly here."),
            ExtractedBlock(kind="bullet", text="Another complete thought stated plainly."),
        )
        assert "full stop" in build_tone_profile(document).bullet_style

    def test_collects_verbatim_excerpts(self) -> None:
        long_paragraph = "This sentence is long enough to be a usable style anchor for later."
        assert long_paragraph in build_tone_profile(_doc(_para(long_paragraph))).sample_excerpts

    def test_skips_excerpts_that_are_too_short(self) -> None:
        assert build_tone_profile(_doc(_para("Too short."))).sample_excerpts == []

    def test_empty_document_yields_usable_defaults(self) -> None:
        profile = build_tone_profile(_doc())
        assert profile.avg_sentence_words == 18.0
        assert profile.heading_case == "Title Case"

    def test_renders_a_prompt_block(self) -> None:
        block = build_tone_profile(_doc(_para("We build things. We ship them."))).as_prompt_block()
        assert "Formality:" in block
        assert "Voice:" in block
