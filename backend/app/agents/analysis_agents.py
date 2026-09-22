"""Agents 2 and 3: document analysis and PPT template analysis.

Both answer the same question for their format — *what does this upload look
and sound like?* — and return a profile the renderers can act on. They are
plain functions with explicit arguments rather than classes behind a uniform
``run(state)`` interface, because a supervisor calling nine narrow functions is
easier to read than nine classes pretending to share a shape they do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.app.analysis.docx_profile import build_docx_profile
from backend.app.analysis.pptx_profile import build_pptx_profile
from backend.app.analysis.tone_profile import build_tone_profile
from backend.app.core.errors import UnsupportedFormatError
from backend.app.core.models.template import (
    DocxTemplateProfile,
    PptxTemplateProfile,
    ToneProfile,
)
from backend.app.ingestion.base import ExtractedDocument
from backend.app.ingestion.ocr import Transcriber
from backend.app.ingestion.reader import kind_for_suffix, read_document
from backend.app.llm.base import LLMProvider

DOCUMENT_ANALYSIS_AGENT = "document_analysis"
PPT_ANALYSIS_AGENT = "ppt_analysis"


@dataclass(frozen=True)
class AnalysisResult:
    """What an analysis agent hands back."""

    extracted: ExtractedDocument
    profile: DocxTemplateProfile | PptxTemplateProfile

    @property
    def summary(self) -> str:
        if isinstance(self.profile, PptxTemplateProfile):
            return (
                f"{self.extracted.page_count} slides, "
                f"{len(self.profile.layouts)} layouts, "
                f"{self.profile.aspect_ratio}, "
                f"~{self.profile.avg_bullets_per_slide:.0f} bullets per slide"
            )
        return (
            f"{len(self.profile.outline)} headings, "
            f"body style '{self.profile.body_style}', "
            f"font {self.profile.default_font or 'theme default'}"
            + (", header and footer present" if self.profile.has_header else "")
        )


def analyse_document(
    path: Path,
    display_name: str | None = None,
    provider: LLMProvider | None = None,
    transcriber: Transcriber | None = None,
) -> AnalysisResult:
    """Agent 2. Reads a DOCX, PDF, image or text file and profiles its style.

    Non-DOCX sources still produce a ``DocxTemplateProfile``: a PDF or a scan
    carries tone and outline but no usable Word styles, so the profile falls
    back to safe built-in style names while keeping the measured tone. That is
    what lets a user upload a scanned proposal and still get output that reads
    like it.
    """
    kind = kind_for_suffix(path.suffix)
    if kind == "pptx":
        raise UnsupportedFormatError(
            "A PPTX must be analysed by the PPT analysis agent.",
            public_message="Use presentation analysis for a PPTX file.",
        )

    extracted = read_document(path, display_name, transcriber)
    tone = build_tone_profile(extracted, provider)

    if kind == "docx":
        profile = build_docx_profile(path, extracted, tone)
    else:
        profile = _profile_without_word_styles(extracted, tone)

    return AnalysisResult(extracted=extracted, profile=profile)


def analyse_presentation(
    path: Path,
    display_name: str | None = None,
    provider: LLMProvider | None = None,
) -> AnalysisResult:
    """Agent 3. Reads a PPTX and profiles its layouts, theme and rhythm."""
    if kind_for_suffix(path.suffix) != "pptx":
        raise UnsupportedFormatError(
            f"Presentation analysis needs a PPTX, got {path.suffix!r}.",
            public_message="Presentation analysis only accepts .pptx files.",
        )

    extracted = read_document(path, display_name)
    tone = build_tone_profile(extracted, provider)
    profile = build_pptx_profile(path, extracted, tone)

    return AnalysisResult(extracted=extracted, profile=profile)


def _profile_without_word_styles(
    extracted: ExtractedDocument, tone: ToneProfile
) -> DocxTemplateProfile:
    """Profile for a source that has no Word style table of its own.

    The style names here are python-docx defaults, which are guaranteed present
    in the blank document the renderer falls back to when there is no DOCX
    template to inherit from.
    """
    return DocxTemplateProfile(
        source_filename=extracted.source_filename,
        heading_styles={level: f"Heading {level}" for level in range(1, 5)},
        title_style="Title",
        body_style="Normal",
        bullet_style="List Bullet",
        outline=extracted.outline(),
        tone=tone,
    )
