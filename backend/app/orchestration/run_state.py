"""What one turn carries.

``RunState`` is assembled by the chat route and handed to the orchestrator. It
is a plain dataclass rather than a framework state object: the agents each take
the few fields they need as arguments, so nothing depends on the container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.core.models.citation import CitationAllocator, ResearchFindings
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile
from backend.app.db.models import Artifact, Upload
from backend.app.llm.base import LLMProvider
from backend.app.rag.store import VectorStore
from backend.app.search.provider import SearchProvider


@dataclass
class TemplateRef:
    """An uploaded template plus the profile taken from it."""

    upload_id: str
    display_name: str
    path: Path
    profile: DocxTemplateProfile | PptxTemplateProfile


@dataclass
class RunState:
    conversation_id: str
    user_message: str

    settings: Settings
    session: Session
    provider: LLMProvider
    search_provider: SearchProvider
    store: VectorStore

    docx_template: TemplateRef | None = None
    pptx_template: TemplateRef | None = None
    existing_artifacts: list[Artifact] = field(default_factory=list)

    allocator: CitationAllocator = field(default_factory=CitationAllocator)
    findings: ResearchFindings = field(default_factory=ResearchFindings)

    def template_names(self) -> list[str]:
        return [
            ref.display_name
            for ref in (self.docx_template, self.pptx_template)
            if ref is not None
        ]

    def artifact_titles(self) -> list[str]:
        """Includes the id, so the supervisor can name an edit target."""
        return [
            f"{a.id} - {a.title} ({a.kind}, v{a.current_version})"
            for a in self.existing_artifacts
        ]

    def document_profile(self) -> DocxTemplateProfile:
        """The DOCX profile to write with, or a safe default."""
        if self.docx_template and isinstance(self.docx_template.profile, DocxTemplateProfile):
            return self.docx_template.profile
        return DocxTemplateProfile(
            source_filename="none",
            heading_styles={level: f"Heading {level}" for level in range(1, 5)},
            title_style="Title",
        )

    def deck_profile(self) -> PptxTemplateProfile:
        """The PPTX profile to build with, or a safe default."""
        if self.pptx_template and isinstance(self.pptx_template.profile, PptxTemplateProfile):
            return self.pptx_template.profile
        return PptxTemplateProfile(source_filename="none")

    def document_template_path(self) -> Path | None:
        return self.docx_template.path if self.docx_template else None

    def deck_template_path(self) -> Path | None:
        return self.pptx_template.path if self.pptx_template else None


def templates_from_uploads(uploads: list[Upload]) -> tuple[TemplateRef | None, TemplateRef | None]:
    """Pick the most recent DOCX-ish and PPTX template from a conversation.

    The newest wins, so re-uploading a template replaces it without the user
    having to delete the old one.
    """
    docx_ref: TemplateRef | None = None
    pptx_ref: TemplateRef | None = None

    for upload in uploads:
        if upload.profile is None:
            continue

        if upload.kind == "pptx" and pptx_ref is None:
            pptx_ref = TemplateRef(
                upload_id=upload.id,
                display_name=upload.display_name,
                path=Path(upload.stored_path),
                profile=PptxTemplateProfile.model_validate(upload.profile),
            )
        elif upload.kind != "pptx" and docx_ref is None:
            docx_ref = TemplateRef(
                upload_id=upload.id,
                display_name=upload.display_name,
                path=Path(upload.stored_path),
                profile=DocxTemplateProfile.model_validate(upload.profile),
            )

    return docx_ref, pptx_ref
