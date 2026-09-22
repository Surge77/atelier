"""DOCX reading, checked against the real sample template."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from backend.app.ingestion.docx_reader import read_docx

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "templates" / "Company_Proposal.docx"


@pytest.fixture(scope="module")
def proposal():
    if not SAMPLE.exists():
        pytest.skip("Run scripts/make_samples.py to generate the sample templates.")
    return read_docx(SAMPLE)


class TestSampleProposal:
    def test_identifies_the_source(self, proposal) -> None:
        assert proposal.kind == "docx"
        assert proposal.source_filename == "Company_Proposal.docx"

    def test_recovers_the_title(self, proposal) -> None:
        titles = [b.text for b in proposal.blocks_of("title")]
        assert "Northwind Analytics" in titles

    def test_recovers_the_outline_in_document_order(self, proposal) -> None:
        assert proposal.outline() == [
            "Northwind Analytics",
            "Executive Summary",
            "Scope of Work",
            "Commercials",
            "Assumptions",
        ]

    def test_records_heading_levels(self, proposal) -> None:
        by_text = {b.text: b.level for b in proposal.blocks_of("heading")}
        assert by_text["Executive Summary"] == 1
        assert by_text["Assumptions"] == 2

    def test_detects_bullets(self, proposal) -> None:
        bullets = [b.text for b in proposal.blocks_of("bullet")]
        assert "Discovery and data source inventory" in bullets
        assert len(bullets) == 3

    def test_captures_table_structure_not_just_text(self, proposal) -> None:
        tables = proposal.blocks_of("table")
        assert len(tables) == 1
        assert tables[0].rows[0] == ["Workstream", "Duration", "Indicative Fee"]
        assert len(tables[0].rows) == 4

    def test_preserves_the_style_name_each_block_carried(self, proposal) -> None:
        headings = {b.text: b.style_name for b in proposal.blocks_of("heading")}
        assert headings["Executive Summary"] == "Heading 1"

    def test_table_appears_between_its_surrounding_paragraphs(self, proposal) -> None:
        """Body-order traversal, not paragraphs-then-tables."""
        kinds = [b.kind for b in proposal.blocks]
        table_at = kinds.index("table")

        assert "heading" in kinds[:table_at]
        assert "heading" in kinds[table_at:]


class TestEdgeCases:
    def test_skips_empty_paragraphs(self, tmp_path: Path) -> None:
        document = Document()
        document.add_paragraph("Real content")
        document.add_paragraph("   ")
        document.add_paragraph("")
        path = tmp_path / "sparse.docx"
        document.save(str(path))

        assert len(read_docx(path).blocks) == 1

    def test_empty_document_yields_no_blocks(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.docx"
        Document().save(str(path))

        result = read_docx(path)

        assert result.blocks == []
        assert result.text == ""

    def test_display_name_overrides_the_path_name(self, tmp_path: Path) -> None:
        path = tmp_path / "a1b2c3.docx"
        Document().save(str(path))

        assert read_docx(path, "Original Name.docx").source_filename == "Original Name.docx"

    def test_directly_numbered_paragraph_is_read_as_a_bullet(self, tmp_path: Path) -> None:
        """Numbering applied without a list style must still be detected."""
        document = Document()
        document.add_paragraph("Numbered item", style="List Number")
        path = tmp_path / "numbered.docx"
        document.save(str(path))

        assert read_docx(path).blocks[0].kind == "bullet"
