"""Artifact versioning: render a plan and append an immutable snapshot.

Every version records the plan, the ops that produced it from its parent, the
citations resolved at that moment and the rendered file. Nothing is ever
updated in place except the artifact's pointer to its newest version, so the
full history stays downloadable and a diff between two versions is a diff
between two plans.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.core.errors import NotFoundError
from backend.app.core.logging import get_logger
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import ContentPlan, DeckPlan
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile
from backend.app.db.models import Artifact, ArtifactVersion
from backend.app.rendering.docx_renderer import render_document
from backend.app.rendering.pptx_renderer import render_deck
from backend.app.services.storage import resolve_within

logger = get_logger(__name__)

MAX_FILENAME_STEM = 60


def create_artifact(
    session: Session,
    conversation_id: str,
    kind: str,
    title: str,
    template_upload_id: str | None = None,
) -> Artifact:
    artifact = Artifact(
        conversation_id=conversation_id,
        kind=kind,
        title=title,
        template_upload_id=template_upload_id,
    )
    session.add(artifact)
    session.flush()
    return artifact


def add_version(
    session: Session,
    settings: Settings,
    artifact: Artifact,
    plan: ContentPlan,
    profile: DocxTemplateProfile | PptxTemplateProfile,
    citations: list[Citation],
    template_path: Path | None = None,
    ops: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
    summary: str = "",
) -> ArtifactVersion:
    """Render ``plan`` and record it as the artifact's next version."""
    parent = latest_version(artifact)
    version_no = artifact.current_version + 1

    destination = _version_path(settings, artifact, plan, version_no)
    _render(plan, profile, destination, template_path, citations)

    version = ArtifactVersion(
        artifact_id=artifact.id,
        version_no=version_no,
        parent_version_id=parent.id if parent else None,
        plan=plan.model_dump(mode="json"),
        ops=ops or [],
        citations=[citation.model_dump(mode="json") for citation in citations],
        validation=validation,
        file_path=str(destination),
        summary=summary,
    )
    session.add(version)

    artifact.current_version = version_no
    artifact.title = plan.title or artifact.title
    session.flush()

    logger.info("Wrote %s v%s -> %s", artifact.title, version_no, destination.name)
    return version


def latest_version(artifact: Artifact) -> ArtifactVersion | None:
    return artifact.versions[-1] if artifact.versions else None


def get_version(artifact: Artifact, version_no: int | None) -> ArtifactVersion:
    """A specific version, or the newest when ``version_no`` is None."""
    if version_no is None:
        version = latest_version(artifact)
    else:
        version = next((v for v in artifact.versions if v.version_no == version_no), None)

    if version is None:
        raise NotFoundError(f"Artifact {artifact.id} has no version {version_no}.")
    return version


def _render(
    plan: ContentPlan,
    profile: DocxTemplateProfile | PptxTemplateProfile,
    destination: Path,
    template_path: Path | None,
    citations: list[Citation],
) -> Path:
    if isinstance(plan, DeckPlan):
        if not isinstance(profile, PptxTemplateProfile):
            raise TypeError("A deck must be rendered with a PPTX profile.")
        return render_deck(plan, profile, destination, template_path, citations)

    if not isinstance(profile, DocxTemplateProfile):
        raise TypeError("A document must be rendered with a DOCX profile.")
    return render_document(plan, profile, destination, template_path, citations)


def _version_path(
    settings: Settings, artifact: Artifact, plan: ContentPlan, version_no: int
) -> Path:
    """``artifacts/<artifact id>/v<n>_<slug>.<ext>``.

    The artifact id is a generated hex string and the slug is derived from the
    title with everything unsafe stripped, so no user-controlled text ever
    becomes a path component verbatim.
    """
    extension = "pptx" if isinstance(plan, DeckPlan) else "docx"
    return resolve_within(
        settings.artifacts_dir,
        artifact.id,
        f"v{version_no}_{_slug(plan.title or artifact.kind)}.{extension}",
    )


def _slug(text: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in text).strip("_")
    return (cleaned[:MAX_FILENAME_STEM] or "artifact").lower()
