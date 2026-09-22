"""PPTX, PDF, image and text reading, plus format dispatch.

The OCR path is exercised with a stub transcriber, so no vision model is
called and the tests stay fast and offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from backend.app.core.errors import UnsupportedFormatError
from backend.app.ingestion.ocr import parse_transcript
from backend.app.ingestion.pdf_reader import read_pdf
from backend.app.ingestion.pptx_reader import read_pptx
from backend.app.ingestion.reader import kind_for_suffix, read_document

SAMPLE_PPTX = (
    Path(__file__).resolve().parents[3] / "samples" / "templates" / "Company_Template.pptx"
)


def _blank_pdf(path: Path, pages: int = 2) -> Path:
    """A PDF with no text layer — the shape of a scan."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


class TestPptxReader:
    @pytest.fixture(scope="class")
    def deck(self):
        if not SAMPLE_PPTX.exists():
            pytest.skip("Run scripts/make_samples.py first.")
        return read_pptx(SAMPLE_PPTX)

    def test_counts_slides(self, deck) -> None:
        assert deck.page_count == 3

    def test_reads_slide_titles_in_order(self, deck) -> None:
        assert deck.outline() == ["Northwind Analytics", "How We Work", "Engagement Model"]

    def test_records_the_layout_name_on_the_title(self, deck) -> None:
        titles = deck.blocks_of("slide_title")
        assert titles[0].style_name is not None

    def test_reads_body_bullets(self, deck) -> None:
        bullets = [b.text for b in deck.blocks_of("bullet")]
        assert "Governed data by default" in bullets

    def test_tags_each_block_with_its_slide_number(self, deck) -> None:
        assert {b.location for b in deck.blocks} == {1, 2, 3}

    def test_does_not_duplicate_the_title_as_a_bullet(self, deck) -> None:
        bullets = [b.text for b in deck.blocks_of("bullet")]
        assert "How We Work" not in bullets


class TestPdfReader:
    def test_detects_a_scan_and_uses_the_transcriber(self, tmp_path: Path) -> None:
        pdf = _blank_pdf(tmp_path / "scan.pdf")
        calls: list[bytes] = []

        def stub(image_png: bytes) -> str:
            calls.append(image_png)
            return "# Invoice\n- Line item one\nTotal due on receipt"

        result = read_pdf(pdf, transcriber=stub)

        assert result.used_ocr is True
        assert len(calls) == 2  # one render per page
        assert result.outline() == ["Invoice", "Invoice"]

    def test_falls_back_to_the_sparse_text_layer_without_a_transcriber(
        self, tmp_path: Path
    ) -> None:
        """A missing vision model must degrade, not raise."""
        pdf = _blank_pdf(tmp_path / "scan.pdf", pages=1)

        result = read_pdf(pdf)

        assert result.used_ocr is False
        assert result.page_count == 1

    def test_reports_the_true_page_count_when_ocr_is_capped(self, tmp_path: Path) -> None:
        pdf = _blank_pdf(tmp_path / "long.pdf", pages=14)

        result = read_pdf(pdf, transcriber=lambda _: "text")

        assert result.page_count == 14


class TestParseTranscript:
    def test_reads_heading_markers(self) -> None:
        blocks = parse_transcript("# Executive Summary\nSome prose.")
        assert blocks[0].kind == "heading"
        assert blocks[0].text == "Executive Summary"
        assert blocks[1].kind == "paragraph"

    def test_reads_heading_depth(self) -> None:
        assert parse_transcript("### Deep")[0].level == 3

    def test_caps_heading_depth_at_four(self) -> None:
        assert parse_transcript("####### Very deep")[0].level == 4

    @pytest.mark.parametrize("marker", ["- ", "* ", "• "])
    def test_reads_bullet_markers(self, marker: str) -> None:
        blocks = parse_transcript(f"{marker}An item")
        assert blocks[0].kind == "bullet"
        assert blocks[0].text == "An item"

    def test_skips_blank_lines(self) -> None:
        assert len(parse_transcript("One\n\n\nTwo")) == 2

    def test_empty_transcript_yields_nothing(self) -> None:
        assert parse_transcript("   \n  ") == []

    def test_tags_blocks_with_the_page_number(self) -> None:
        assert parse_transcript("text", location=7)[0].location == 7


class TestDispatch:
    @pytest.mark.parametrize(
        ("suffix", "expected"),
        [
            (".docx", "docx"),
            (".pptx", "pptx"),
            (".pdf", "pdf"),
            (".png", "image"),
            (".JPG", "image"),
            (".md", "text"),
        ],
    )
    def test_maps_suffix_to_kind(self, suffix: str, expected: str) -> None:
        assert kind_for_suffix(suffix) == expected

    def test_rejects_an_unknown_suffix(self) -> None:
        with pytest.raises(UnsupportedFormatError):
            kind_for_suffix(".exe")

    def test_reads_markdown_structure(self, tmp_path: Path) -> None:
        path = tmp_path / "notes.md"
        path.write_text("# Title\n\n- point one\n\nA paragraph.\n", encoding="utf-8")

        result = read_document(path)

        assert [b.kind for b in result.blocks] == ["heading", "bullet", "paragraph"]

    def test_image_without_a_transcriber_is_a_clear_error(self, tmp_path: Path) -> None:
        from PIL import Image

        path = tmp_path / "scan.png"
        Image.new("RGB", (10, 10), "white").save(path)

        with pytest.raises(UnsupportedFormatError, match="vision"):
            read_document(path)

    def test_reads_an_image_through_the_transcriber(self, tmp_path: Path) -> None:
        from PIL import Image

        path = tmp_path / "scan.png"
        Image.new("RGB", (10, 10), "white").save(path)

        result = read_document(path, transcriber=lambda _: "# Heading\nBody text")

        assert result.kind == "image"
        assert result.used_ocr is True
        assert result.outline() == ["Heading"]
