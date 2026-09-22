"""Vision OCR for scanned PDFs and images.

Pages are rasterised with pypdfium2 (BSD/Apache, ships a Windows wheel, needs
no system binary) and transcribed by the configured vision model. Using the
vision model rather than Tesseract avoids a native dependency and handles
layout — tables, columns, headings — markedly better on real scans.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Protocol

from PIL import Image

from backend.app.core.errors import ProviderError
from backend.app.core.logging import get_logger
from backend.app.ingestion.base import ExtractedBlock
from backend.app.llm.base import LLMProvider

logger = get_logger(__name__)

# 2x gives the vision model enough detail on body text without ballooning the
# payload; 1x loses small print and 3x mostly costs tokens.
RENDER_SCALE = 2

# Rendering every page of a long scan is slow and rarely adds signal for style
# and tone analysis, which is what ingestion feeds.
MAX_OCR_PAGES = 10

TRANSCRIBE_PROMPT = (
    "Transcribe this page exactly as written. Preserve headings, list items and "
    "table rows on separate lines. Mark a heading by prefixing it with '# '. "
    "Mark a list item by prefixing it with '- '. Do not summarise, explain or "
    "add commentary. If the page is blank, reply with nothing."
)


class Transcriber(Protocol):
    """Anything that can turn a page image into text.

    Declared as a Protocol so ingestion can be tested with a stub and never
    needs a real vision model in unit tests.
    """

    # Positional-only: callers pass plain functions whose parameter name
    # need not match.
    def __call__(self, image_png: bytes, /) -> str: ...


class VisionTranscriber:
    """Transcribes page images using the configured LLM provider."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    def __call__(self, image_png: bytes) -> str:
        provider = self._provider
        if provider is None:
            # Resolved lazily so constructing a transcriber never requires
            # a configured API key.
            from backend.app.llm.factory import get_provider

            provider = get_provider()

        encoded = base64.b64encode(image_png).decode("ascii")
        return provider.describe_image(TRANSCRIBE_PROMPT, encoded, "image/png").strip()


def render_pdf_pages(path: Path, max_pages: int = MAX_OCR_PAGES) -> list[bytes]:
    """Rasterise the first ``max_pages`` pages of a PDF to PNG bytes."""
    import pypdfium2 as pdfium

    images: list[bytes] = []
    document = pdfium.PdfDocument(str(path))
    try:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            bitmap = page.render(scale=RENDER_SCALE)
            images.append(_to_png(bitmap.to_pil()))
    finally:
        document.close()

    return images


def load_image_as_png(path: Path) -> bytes:
    """Normalise any supported image to PNG so one media type reaches the model."""
    with Image.open(path) as image:
        return _to_png(image.convert("RGB"))


def _to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def parse_transcript(text: str, location: int = 1) -> list[ExtractedBlock]:
    """Turn a transcript into blocks using the markers the prompt asked for.

    The prompt requests '# ' for headings and '- ' for list items, so the
    transcription arrives with structure already marked rather than as a wall
    of text that would have to be re-segmented by another model call.
    """
    blocks: list[ExtractedBlock] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            depth = len(line) - len(line.lstrip("#"))
            heading = line.lstrip("#").strip()
            if heading:
                blocks.append(
                    ExtractedBlock(
                        kind="heading", text=heading, level=min(depth, 4), location=location
                    )
                )
        elif line.startswith(("- ", "* ", "• ")):
            blocks.append(
                ExtractedBlock(kind="bullet", text=line[2:].strip(), location=location)
            )
        else:
            blocks.append(ExtractedBlock(kind="paragraph", text=line, location=location))

    return blocks


def transcribe_images(images: list[bytes], transcriber: Transcriber) -> list[str]:
    """Transcribe each page, tolerating a failure on any single one.

    One unreadable page should degrade the result, not fail the whole upload,
    so failures are logged and skipped.
    """
    transcripts: list[str] = []

    for number, image in enumerate(images, start=1):
        try:
            transcripts.append(transcriber(image))
        except ProviderError as exc:
            logger.warning("OCR failed on page %s, skipping it: %s", number, exc)
            transcripts.append("")

    return transcripts
