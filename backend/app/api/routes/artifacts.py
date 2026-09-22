"""Artifact listing, version history, download and conversion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.core.errors import NotFoundError
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import DocumentPlan, plan_from_dict
from backend.app.core.models.template import DocxTemplateProfile, PptxTemplateProfile
from backend.app.db.models import Artifact, ArtifactVersion
from backend.app.db.session import get_session
from backend.app.services.conversion import deck_to_document, document_to_deck
from backend.app.services.storage import resolve_within, sanitize_display_name
from backend.app.services.versioning import add_version, create_artifact, get_version

router = APIRouter(tags=["artifacts"], prefix="/artifacts")

MEDIA_TYPES = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "deck": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class VersionView(BaseModel):
    version_no: int
    summary: str
    created_at: str
    ops: list[dict[str, Any]]
    citation_count: int
    validation: dict[str, Any] | None


class ArtifactView(BaseModel):
    id: str
    kind: str
    title: str
    current_version: int
    conversation_id: str


class ArtifactDetail(ArtifactView):
    versions: list[VersionView]
    plan: dict[str, Any]
    citations: list[dict[str, Any]]


@router.get("", response_model=list[ArtifactView])
def list_artifacts(
    conversation_id: str | None = None, session: Session = Depends(get_session)
) -> list[ArtifactView]:
    query = session.query(Artifact)
    if conversation_id:
        query = query.filter(Artifact.conversation_id == conversation_id)

    return [_to_view(a) for a in query.order_by(Artifact.created_at.desc()).all()]


@router.get("/{artifact_id}", response_model=ArtifactDetail)
def get_artifact(artifact_id: str, session: Session = Depends(get_session)) -> ArtifactDetail:
    artifact = _load(session, artifact_id)
    latest = get_version(artifact, None)

    return ArtifactDetail(
        **_to_view(artifact).model_dump(),
        versions=[_to_version_view(v) for v in artifact.versions],
        plan=latest.plan,
        citations=latest.citations,
    )


@router.get("/{artifact_id}/versions/{version_no}/download")
def download_version(
    artifact_id: str,
    version_no: int,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> FileResponse:
    """Serve any version's file, not only the newest."""
    artifact = _load(session, artifact_id)
    version = get_version(artifact, version_no)

    # The stored path is re-resolved inside the artifacts directory rather than
    # trusted, so a tampered database row cannot read an arbitrary file.
    path = resolve_within(settings.artifacts_dir, *Path(version.file_path).parts[-2:])
    if not path.exists():
        raise NotFoundError(f"The file for v{version_no} is missing from disk.")

    extension = "pptx" if artifact.kind == "deck" else "docx"
    filename = f"{sanitize_display_name(artifact.title)}_v{version_no}.{extension}"

    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(artifact.kind, "application/octet-stream"),
        filename=filename,
    )


@router.post("/{artifact_id}/convert", response_model=ArtifactView, status_code=201)
def convert_artifact(
    artifact_id: str,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> ArtifactView:
    """Convert a document into a deck, or a deck into a document.

    A pure transform of the content plan, so no model call and no cost.
    """
    artifact = _load(session, artifact_id)
    version = get_version(artifact, None)
    plan = plan_from_dict(version.plan)

    if isinstance(plan, DocumentPlan):
        converted, kind = document_to_deck(plan), "deck"
        profile: Any = PptxTemplateProfile(source_filename="converted")
    else:
        converted, kind = deck_to_document(plan), "document"
        profile = DocxTemplateProfile(
            source_filename="converted",
            heading_styles={level: f"Heading {level}" for level in range(1, 5)},
            title_style="Title",
        )

    new_artifact = create_artifact(session, artifact.conversation_id, kind, converted.title)
    add_version(
        session,
        settings,
        new_artifact,
        converted,
        profile,
        [Citation.model_validate(row) for row in version.citations],
        summary=f"Converted from {artifact.kind} {artifact.title!r}",
    )

    return _to_view(new_artifact)


def _load(session: Session, artifact_id: str) -> Artifact:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"No artifact {artifact_id!r}.")
    return artifact


def _to_view(artifact: Artifact) -> ArtifactView:
    return ArtifactView(
        id=artifact.id,
        kind=artifact.kind,
        title=artifact.title,
        current_version=artifact.current_version,
        conversation_id=artifact.conversation_id,
    )


def _to_version_view(version: ArtifactVersion) -> VersionView:
    return VersionView(
        version_no=version.version_no,
        summary=version.summary,
        created_at=version.created_at.isoformat(),
        ops=version.ops or [],
        citation_count=len(version.citations or []),
        validation=version.validation,
    )
