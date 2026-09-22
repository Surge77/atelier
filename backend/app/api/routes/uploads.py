"""Upload and template-analysis endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.core.errors import NotFoundError
from backend.app.db.models import Upload
from backend.app.db.session import get_session
from backend.app.ingestion.ocr import VisionTranscriber
from backend.app.services.uploads import ingest_upload

router = APIRouter(tags=["uploads"])


class UploadResponse(BaseModel):
    id: str
    display_name: str
    kind: str
    size_bytes: int
    page_count: int
    used_ocr: bool
    summary: str
    profile: dict[str, Any]


class UploadSummary(BaseModel):
    id: str
    display_name: str
    kind: str
    size_bytes: int


@router.post("/uploads", response_model=UploadResponse, status_code=201)
def create_upload(
    file: UploadFile = File(...),
    conversation_id: str | None = None,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> UploadResponse:
    """Accept a template or source document and return its style profile.

    Analysis runs inline rather than in a background job: the caller cannot do
    anything useful until the profile exists, and a POC gains nothing from a
    queue it would then have to poll.
    """
    upload, _analysis = ingest_upload(
        data=file.file.read(),
        original_name=file.filename or "upload",
        settings=settings,
        session=session,
        conversation_id=conversation_id,
        # Vision is resolved lazily, so an upload that needs no OCR never
        # requires a configured API key.
        transcriber=VisionTranscriber(),
    )

    return _to_response(upload)


@router.get("/uploads", response_model=list[UploadSummary])
def list_uploads(
    conversation_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[UploadSummary]:
    # With no conversation, show only uploads not yet claimed by one. Those
    # are exactly the files the next turn will adopt; returning every upload
    # ever made would show a new conversation someone else's templates.
    query = session.query(Upload)
    if conversation_id:
        query = query.filter(Upload.conversation_id == conversation_id)
    else:
        query = query.filter(Upload.conversation_id.is_(None))

    return [
        UploadSummary(
            id=upload.id,
            display_name=upload.display_name,
            kind=upload.kind,
            size_bytes=upload.size_bytes,
        )
        for upload in query.order_by(Upload.created_at.desc()).all()
    ]


@router.get("/uploads/{upload_id}", response_model=UploadResponse)
def get_upload(upload_id: str, session: Session = Depends(get_session)) -> UploadResponse:
    upload = session.get(Upload, upload_id)
    if upload is None:
        raise NotFoundError(f"No upload {upload_id!r}.")

    return _to_response(upload)


def _to_response(upload: Upload) -> UploadResponse:
    return UploadResponse(
        id=upload.id,
        display_name=upload.display_name,
        kind=upload.kind,
        size_bytes=upload.size_bytes,
        page_count=upload.page_count,
        used_ocr=upload.used_ocr,
        summary=upload.summary,
        profile=upload.profile or {},
    )
