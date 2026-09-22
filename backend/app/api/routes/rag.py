"""Enterprise knowledge base endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.core.errors import NotFoundError
from backend.app.db.models import RagDocument
from backend.app.db.session import get_session
from backend.app.ingestion.ocr import VisionTranscriber
from backend.app.rag.factory import get_store
from backend.app.rag.ingest import ingest_into_knowledge_base, remove_from_knowledge_base

router = APIRouter(tags=["rag"], prefix="/rag")


class IngestResponse(BaseModel):
    document_id: str
    display_name: str
    chunk_count: int
    total_chunks_in_store: int


class DocumentSummary(BaseModel):
    id: str
    display_name: str
    chunk_count: int


class RetrievedChunk(BaseModel):
    chunk_id: str
    text: str
    score: float
    document_name: str
    heading: str
    locator: str


@router.post("/documents", response_model=IngestResponse, status_code=201)
def ingest_document(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> IngestResponse:
    """Add a document to the enterprise corpus the RAG agent searches."""
    result = ingest_into_knowledge_base(
        data=file.file.read(),
        original_name=file.filename or "document",
        settings=settings,
        session=session,
        store=get_store(),
        transcriber=VisionTranscriber(),
    )
    return IngestResponse(**result.__dict__)


@router.get("/documents", response_model=list[DocumentSummary])
def list_documents(session: Session = Depends(get_session)) -> list[DocumentSummary]:
    return [
        DocumentSummary(
            id=row.id, display_name=row.display_name, chunk_count=row.chunk_count
        )
        for row in session.query(RagDocument).order_by(RagDocument.created_at.desc()).all()
    ]


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: str, session: Session = Depends(get_session)) -> None:
    if not remove_from_knowledge_base(document_id, session, get_store()):
        raise NotFoundError(f"No indexed document {document_id!r}.")


@router.get("/search", response_model=list[RetrievedChunk])
def search_knowledge_base(
    q: str = Query(min_length=2, max_length=1000),
    top_k: int = Query(default=6, ge=1, le=25),
) -> list[RetrievedChunk]:
    """Retrieval on its own, so the corpus can be inspected without a chat turn."""
    return [
        RetrievedChunk(
            chunk_id=hit.chunk_id,
            text=hit.text,
            score=hit.score,
            document_name=hit.document_name,
            heading=hit.heading,
            locator=hit.locator,
        )
        for hit in get_store().search(q, top_k)
    ]
