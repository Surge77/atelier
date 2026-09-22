"""PDF reader with an automatic OCR fallback.

A PDF either carries a real text layer or is a scan. Rather than asking the
user which it is, the embedded text is extracted first and the page is treated
as scanned only when that text is too sparse to be genuine. That keeps the
fast, free path for digital PDFs and reserves the vision model for pages that
actually need it.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from backend.app.core.logging import get_logger
from backend.app.ingestion.base import ExtractedBlock, ExtractedDocument
from backend.app.ingestion.ocr import (
    MAX_OCR_PAGES,
    Transcriber,
    parse_transcript,
    render_pdf_pages,
    transcribe_images,
)

logger = get_logger(__name__)

# Below this many characters per page the text layer is missing or decorative,
# which is the signature of a scan.
MIN_CHARS_PER_PAGE = 80

# Heuristic for a line that is a heading in a text-layer PDF: short, and not
# ended like a sentence. Crude, but it only feeds outline detection.
MAX_HEADING_WORDS = 12


def read_pdf(
    path: Path,
    display_name: str | None = None,
    transcriber: Transcriber | None = None,
) -> ExtractedDocument:
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    page_count = len(pages)

    if _looks_scanned(pages) and transcriber is not None:
        return _read_scanned(path, display_name or path.name, page_count, transcriber)

    if _looks_scanned(pages):
        logger.warning(
            "%s looks scanned but no transcriber was supplied; "
            "returning the sparse text layer.",
            path.name,
        )

    blocks = [
        block
        for number, text in enumerate(pages, start=1)
        for block in _blocks_from_text(text, number)
    ]
    return ExtractedDocument(
        source_filename=display_name or path.name,
        kind="pdf",
        blocks=blocks,
        page_count=page_count,
    )


def _looks_scanned(pages: list[str]) -> bool:
    if not pages:
        return False
    average = sum(len(text) for text in pages) / len(pages)
    return average < MIN_CHARS_PER_PAGE


def _read_scanned(
    path: Path, display_name: str, page_count: int, transcriber: Transcriber
) -> ExtractedDocument:
    logger.info("Treating %s as scanned; running vision OCR.", display_name)

    images = render_pdf_pages(path, max_pages=MAX_OCR_PAGES)
    transcripts = transcribe_images(images, transcriber)
    blocks = [
        block
        for number, transcript in enumerate(transcripts, start=1)
        for block in parse_transcript(transcript, number)
    ]

    return ExtractedDocument(
        source_filename=display_name,
        kind="pdf",
        blocks=blocks,
        page_count=page_count,
        used_ocr=True,
    )


def _blocks_from_text(page_text: str, page_number: int) -> list[ExtractedBlock]:
    blocks: list[ExtractedBlock] = []

    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # The en dash is deliberate: PDFs commonly use it as a bullet glyph.
        if line.startswith(("•", "-", "*", "–")):  # noqa: RUF001
            blocks.append(
                ExtractedBlock(kind="bullet", text=line[1:].strip(), location=page_number)
            )
        elif _looks_like_heading(line):
            blocks.append(ExtractedBlock(kind="heading", text=line, level=1, location=page_number))
        else:
            blocks.append(ExtractedBlock(kind="paragraph", text=line, location=page_number))

    return blocks


def _looks_like_heading(line: str) -> bool:
    words = line.split()
    if not words or len(words) > MAX_HEADING_WORDS:
        return False
    if line.endswith((".", ",", ";", ":")):
        return False
    return line.isupper() or line.istitle()
