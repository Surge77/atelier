"""Single entry point for reading any supported upload.

Callers pass a path and get an ``ExtractedDocument`` back. Which reader runs is
an implementation detail, so adding a format touches only this dispatch table.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.core.errors import UnsupportedFormatError
from backend.app.ingestion.base import ExtractedBlock, ExtractedDocument, SourceKind
from backend.app.ingestion.docx_reader import read_docx
from backend.app.ingestion.image_reader import read_image
from backend.app.ingestion.ocr import Transcriber
from backend.app.ingestion.pdf_reader import read_pdf
from backend.app.ingestion.pptx_reader import read_pptx

_KIND_BY_SUFFIX: dict[str, SourceKind] = {
    ".docx": "docx",
    ".pptx": "pptx",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".txt": "text",
    ".md": "text",
}


def kind_for_suffix(suffix: str) -> SourceKind:
    kind = _KIND_BY_SUFFIX.get(suffix.lower())
    if kind is None:
        raise UnsupportedFormatError(f"No reader for suffix {suffix!r}.")
    return kind


def read_document(
    path: Path,
    display_name: str | None = None,
    transcriber: Transcriber | None = None,
) -> ExtractedDocument:
    """Read any supported file.

    ``transcriber`` is only consulted for scans and images. Passing it
    explicitly keeps the readers free of any hidden dependency on a configured
    LLM, which is what lets them be unit-tested without credentials.
    """
    kind = kind_for_suffix(path.suffix)

    if kind == "docx":
        return read_docx(path, display_name)
    if kind == "pptx":
        return read_pptx(path, display_name)
    if kind == "pdf":
        return read_pdf(path, display_name, transcriber)
    if kind == "image":
        return read_image(path, display_name, transcriber)
    return _read_text(path, display_name)


def _read_text(path: Path, display_name: str | None) -> ExtractedDocument:
    """Plain text and Markdown, with ATX headings and list markers honoured."""
    blocks: list[ExtractedBlock] = []

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            depth = len(line) - len(line.lstrip("#"))
            blocks.append(
                ExtractedBlock(kind="heading", text=line.lstrip("#").strip(), level=min(depth, 4))
            )
        elif line.startswith(("- ", "* ", "+ ")):
            blocks.append(ExtractedBlock(kind="bullet", text=line[2:].strip()))
        else:
            blocks.append(ExtractedBlock(kind="paragraph", text=line))

    return ExtractedDocument(
        source_filename=display_name or path.name, kind="text", blocks=blocks
    )
