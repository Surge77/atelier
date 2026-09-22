"""Safe filesystem handling for uploads and generated artifacts.

Two rules hold everywhere in the app:

1. A caller-supplied string is never used as a path component. Uploads are
   stored under a generated UUID; the original name is metadata only.
2. Every path is resolved and checked to be inside its base directory before
   it is opened, so a crafted name cannot escape the data directory.
"""

from __future__ import annotations

import hashlib
import re
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from backend.app.config import ALLOWED_UPLOAD_SUFFIXES, MAX_UPLOAD_BYTES
from backend.app.core.errors import InputValidationError, UploadRejectedError

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")
_MAX_DISPLAY_NAME = 120

# Leading bytes that must be present for a given extension. ZIP-based Office
# formats share a signature, so they are disambiguated by archive contents.
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".docx": (b"PK\x03\x04",),
    ".pptx": (b"PK\x03\x04",),
}

# Marker entry that proves which Office format a ZIP archive really is.
_OOXML_MARKERS = {".docx": "word/", ".pptx": "ppt/"}


@dataclass(frozen=True)
class StoredFile:
    """A file accepted into the data directory."""

    file_id: str
    stored_path: Path
    display_name: str
    suffix: str
    size_bytes: int
    sha256: str


def resolve_within(base: Path, *parts: str) -> Path:
    """Join ``parts`` onto ``base`` and refuse anything that escapes it.

    This is the only sanctioned way to turn a stored identifier into a path.
    """
    if not parts or any(not str(part).strip() for part in parts):
        raise InputValidationError("Empty path component.")

    base_resolved = base.resolve()
    candidate = base_resolved.joinpath(*(str(part) for part in parts)).resolve()

    if not candidate.is_relative_to(base_resolved):
        raise InputValidationError(
            f"Path escapes its base directory: {candidate}",
            public_message="Invalid file reference.",
        )
    return candidate


def sanitize_display_name(name: str) -> str:
    """Reduce a user-supplied filename to something safe to show and log.

    The result is never used as a path — ``save_upload`` always writes to a
    UUID — but it still gets stripped so it cannot smuggle separators or
    control characters into a log line or an HTTP header.
    """
    stem = Path(name.replace("\\", "/")).name
    cleaned = _UNSAFE_CHARS.sub("_", stem).strip(" .")
    return (cleaned or "untitled")[:_MAX_DISPLAY_NAME]


def suffix_of(name: str) -> str:
    return Path(name.replace("\\", "/")).suffix.lower()


def validate_upload(data: bytes, original_name: str) -> str:
    """Check size, extension and content signature. Returns the suffix.

    Raises ``UploadRejectedError`` with a caller-safe message on any failure.
    """
    if not data:
        raise UploadRejectedError("Empty upload.", public_message="The file is empty.")

    if len(data) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise UploadRejectedError(
            f"Upload is {len(data)} bytes.",
            public_message=f"Files must be {limit_mb} MB or smaller.",
        )

    suffix = suffix_of(original_name)
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        supported = ", ".join(sorted(ALLOWED_UPLOAD_SUFFIXES))
        raise UploadRejectedError(
            f"Rejected suffix {suffix!r}.",
            public_message=f"Unsupported file type. Supported: {supported}.",
        )

    _verify_signature(data, suffix)
    return suffix


def _verify_signature(data: bytes, suffix: str) -> None:
    """Reject a file whose bytes disagree with its extension."""
    expected = _MAGIC_PREFIXES.get(suffix)
    if expected is None:  # .txt / .md carry no signature.
        return

    if not any(data.startswith(prefix) for prefix in expected):
        raise UploadRejectedError(
            f"Content does not match suffix {suffix!r}.",
            public_message="The file contents do not match its extension.",
        )

    marker = _OOXML_MARKERS.get(suffix)
    if marker is not None:
        _verify_ooxml_archive(data, suffix, marker)


def _verify_ooxml_archive(data: bytes, suffix: str, marker: str) -> None:
    """DOCX and PPTX are both ZIPs; check which one this actually is."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise UploadRejectedError(
            f"Corrupt archive: {exc}",
            public_message="The file is not a readable Office document.",
        ) from exc

    if not any(name.startswith(marker) for name in names):
        raise UploadRejectedError(
            f"Archive has no {marker!r} entries for suffix {suffix!r}.",
            public_message=f"The file is not a valid {suffix.lstrip('.').upper()} document.",
        )


def save_upload(data: bytes, original_name: str, base_dir: Path) -> StoredFile:
    """Validate and write an upload under a generated identifier."""
    suffix = validate_upload(data, original_name)

    file_id = uuid.uuid4().hex
    destination = resolve_within(base_dir, f"{file_id}{suffix}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)

    return StoredFile(
        file_id=file_id,
        stored_path=destination,
        display_name=sanitize_display_name(original_name),
        suffix=suffix,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def write_artifact(data: bytes, base_dir: Path, *parts: str) -> Path:
    """Write generated bytes to ``base_dir/parts``, creating directories."""
    destination = resolve_within(base_dir, *parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination
