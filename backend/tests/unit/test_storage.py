"""Storage safety: path containment and upload validation."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from backend.app.core.errors import InputValidationError, UploadRejectedError
from backend.app.services.storage import (
    resolve_within,
    sanitize_display_name,
    save_upload,
    validate_upload,
)

PDF_BYTES = b"%PDF-1.7\n% minimal\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _ooxml_bytes(marker: str) -> bytes:
    """Build a minimal ZIP that looks like a DOCX or PPTX to the sniffer."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{marker}document.xml", "<document/>")
    return buffer.getvalue()


class TestResolveWithin:
    def test_returns_path_inside_base(self, tmp_path: Path) -> None:
        assert resolve_within(tmp_path, "report.docx") == (tmp_path / "report.docx").resolve()

    @pytest.mark.parametrize(
        "hostile",
        [
            "../escaped.txt",
            "../../etc/passwd",
            "sub/../../outside.txt",
            "..\\windows\\system32",
        ],
    )
    def test_rejects_traversal(self, tmp_path: Path, hostile: str) -> None:
        with pytest.raises(InputValidationError):
            resolve_within(tmp_path, hostile)

    def test_rejects_empty_component(self, tmp_path: Path) -> None:
        with pytest.raises(InputValidationError):
            resolve_within(tmp_path, "   ")

    def test_allows_nested_subdirectory(self, tmp_path: Path) -> None:
        result = resolve_within(tmp_path, "artifacts", "v3", "deck.pptx")
        assert result.is_relative_to(tmp_path.resolve())


class TestSanitizeDisplayName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Company_Proposal.docx", "Company_Proposal.docx"),
            ("../../etc/passwd", "passwd"),
            (r"C:\Users\me\deck.pptx", "deck.pptx"),
            ("weird;name|<>.docx", "weird_name___.docx"),
            ("", "untitled"),
            ("   ", "untitled"),
        ],
    )
    def test_strips_paths_and_unsafe_characters(self, raw: str, expected: str) -> None:
        assert sanitize_display_name(raw) == expected

    def test_truncates_long_names(self) -> None:
        assert len(sanitize_display_name("a" * 500 + ".docx")) <= 120


class TestValidateUpload:
    def test_accepts_matching_pdf(self) -> None:
        assert validate_upload(PDF_BYTES, "scan.pdf") == ".pdf"

    def test_accepts_docx_archive(self) -> None:
        assert validate_upload(_ooxml_bytes("word/"), "proposal.docx") == ".docx"

    def test_accepts_pptx_archive(self) -> None:
        assert validate_upload(_ooxml_bytes("ppt/"), "template.pptx") == ".pptx"

    def test_rejects_empty_file(self) -> None:
        with pytest.raises(UploadRejectedError):
            validate_upload(b"", "empty.pdf")

    def test_rejects_unknown_extension(self) -> None:
        with pytest.raises(UploadRejectedError, match="Rejected suffix"):
            validate_upload(b"MZ\x90\x00", "payload.exe")

    def test_rejects_oversized_file(self) -> None:
        with pytest.raises(UploadRejectedError, match="bytes"):
            validate_upload(PDF_BYTES + b"\x00" * (26 * 1024 * 1024), "huge.pdf")

    def test_rejects_extension_content_mismatch(self) -> None:
        """An executable renamed to .pdf must not get through."""
        with pytest.raises(UploadRejectedError, match="does not match"):
            validate_upload(b"MZ\x90\x00" + b"\x00" * 64, "payload.pdf")

    def test_rejects_pptx_that_is_really_a_docx(self) -> None:
        with pytest.raises(UploadRejectedError, match="no 'ppt/'"):
            validate_upload(_ooxml_bytes("word/"), "mislabelled.pptx")

    def test_rejects_corrupt_office_archive(self) -> None:
        with pytest.raises(UploadRejectedError, match="Corrupt archive"):
            validate_upload(b"PK\x03\x04" + b"garbage" * 8, "broken.docx")

    def test_text_files_skip_signature_check(self) -> None:
        assert validate_upload(b"just some notes", "notes.txt") == ".txt"


class TestSaveUpload:
    def test_stores_under_generated_id_not_user_name(self, tmp_path: Path) -> None:
        stored = save_upload(PDF_BYTES, "../../evil.pdf", tmp_path)

        assert stored.stored_path.parent == tmp_path.resolve()
        assert stored.stored_path.name == f"{stored.file_id}.pdf"
        assert stored.display_name == "evil.pdf"
        assert stored.stored_path.read_bytes() == PDF_BYTES

    def test_records_size_and_digest(self, tmp_path: Path) -> None:
        stored = save_upload(PNG_BYTES, "logo.png", tmp_path)

        assert stored.size_bytes == len(PNG_BYTES)
        assert len(stored.sha256) == 64

    def test_two_uploads_of_same_name_do_not_collide(self, tmp_path: Path) -> None:
        first = save_upload(PDF_BYTES, "report.pdf", tmp_path)
        second = save_upload(PDF_BYTES, "report.pdf", tmp_path)

        assert first.stored_path != second.stored_path
