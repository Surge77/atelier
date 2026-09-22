"""Structure-aware chunking."""

from __future__ import annotations

from backend.app.ingestion.base import ExtractedBlock, ExtractedDocument
from backend.app.rag.chunking import chunk_document


def _document(*blocks: ExtractedBlock, kind: str = "docx") -> ExtractedDocument:
    return ExtractedDocument(
        source_filename="handbook.docx",
        kind=kind,  # type: ignore[arg-type]
        blocks=list(blocks),
    )


def _heading(text: str, location: int = 1) -> ExtractedBlock:
    return ExtractedBlock(kind="heading", text=text, level=1, location=location)


def _para(text: str, location: int = 1) -> ExtractedBlock:
    return ExtractedBlock(kind="paragraph", text=text, location=location)


class TestSectionAlignment:
    def test_one_chunk_per_heading(self) -> None:
        document = _document(
            _heading("Travel Policy"),
            _para("Book economy fares for flights under six hours."),
            _heading("Expenses Policy"),
            _para("Submit receipts within thirty days of the expense."),
        )

        chunks = chunk_document(document, "doc1")

        assert len(chunks) == 2
        assert chunks[0].heading == "Travel Policy"
        assert chunks[1].heading == "Expenses Policy"

    def test_heading_text_is_included_in_the_chunk(self) -> None:
        """The heading is the strongest retrieval signal a section has."""
        document = _document(_heading("Travel Policy"), _para("Book economy fares."))

        assert "Travel Policy" in chunk_document(document, "doc1")[0].text

    def test_content_before_any_heading_still_becomes_a_chunk(self) -> None:
        document = _document(_para("An introduction with no heading above it."))

        chunks = chunk_document(document, "doc1")

        assert len(chunks) == 1
        assert chunks[0].heading == ""

    def test_bullets_keep_their_marker(self) -> None:
        document = _document(
            _heading("Rules"), ExtractedBlock(kind="bullet", text="Be on time")
        )

        assert "- Be on time" in chunk_document(document, "doc1")[0].text

    def test_empty_document_produces_no_chunks(self) -> None:
        assert chunk_document(_document(), "doc1") == []

    def test_heading_with_no_body_is_dropped(self) -> None:
        """A heading alone carries nothing worth retrieving."""
        assert chunk_document(_document(_heading("Orphan")), "doc1") == []


class TestSplitting:
    def test_long_section_is_split(self) -> None:
        sentence = "This sentence exists purely to consume the chunk budget. "
        document = _document(_heading("Long"), _para(sentence * 120))

        chunks = chunk_document(document, "doc1", chunk_tokens=100, overlap_tokens=10)

        assert len(chunks) > 1

    def test_split_happens_at_sentence_boundaries(self) -> None:
        sentence = "Alpha beta gamma delta epsilon zeta eta theta iota kappa. "
        document = _document(_para(sentence * 60))

        chunks = chunk_document(document, "doc1", chunk_tokens=60, overlap_tokens=0)

        for chunk in chunks[:-1]:
            assert chunk.text.rstrip().endswith(".")

    def test_overlap_carries_context_forward(self) -> None:
        sentence = "Numbered statement about company policy and its scope. "
        document = _document(_para(sentence * 80))

        with_overlap = chunk_document(document, "d", chunk_tokens=60, overlap_tokens=20)
        without = chunk_document(document, "d", chunk_tokens=60, overlap_tokens=0)

        assert sum(len(c.text) for c in with_overlap) > sum(len(c.text) for c in without)

    def test_a_single_oversized_sentence_still_yields_a_chunk(self) -> None:
        document = _document(_para("word " * 4000))

        assert len(chunk_document(document, "doc1", chunk_tokens=50)) >= 1


class TestMetadata:
    def test_chunk_ids_are_unique_and_namespaced_by_document(self) -> None:
        document = _document(
            _heading("A"), _para("Body of section A."),
            _heading("B"), _para("Body of section B."),
        )

        ids = [chunk.id for chunk in chunk_document(document, "doc42")]

        assert len(set(ids)) == len(ids)
        assert all(chunk_id.startswith("doc42:") for chunk_id in ids)

    def test_metadata_carries_everything_a_citation_needs(self) -> None:
        document = _document(_heading("Policy", 3), _para("Body text here.", 3))

        metadata = chunk_document(document, "doc1")[0].metadata

        assert metadata["document_id"] == "doc1"
        assert metadata["document_name"] == "handbook.docx"
        assert metadata["heading"] == "Policy"
        assert metadata["location"] == "3"

    def test_locator_label_matches_the_source_kind(self) -> None:
        deck = _document(_heading("Agenda", 2), _para("Body.", 2), kind="pptx")

        chunk = chunk_document(deck, "d")[0]

        assert chunk.locator_label("pptx") == "slide 2"
        assert chunk.locator_label("docx") == "page 2"
