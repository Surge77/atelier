"""Image reader. A photographed or screenshotted page is always OCR."""

from __future__ import annotations

from pathlib import Path

from backend.app.core.errors import UnsupportedFormatError
from backend.app.ingestion.base import ExtractedDocument
from backend.app.ingestion.ocr import Transcriber, load_image_as_png, parse_transcript


def read_image(
    path: Path,
    display_name: str | None = None,
    transcriber: Transcriber | None = None,
) -> ExtractedDocument:
    if transcriber is None:
        raise UnsupportedFormatError(
            "An image upload needs a vision transcriber.",
            public_message=(
                "Reading an image requires a configured LLM with vision support. "
                "Set an API key for the selected provider."
            ),
        )

    transcript = transcriber(load_image_as_png(path))

    return ExtractedDocument(
        source_filename=display_name or path.name,
        kind="image",
        blocks=parse_transcript(transcript),
        page_count=1,
        used_ocr=True,
    )
