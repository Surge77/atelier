"""Ingesting a document into the enterprise knowledge base."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.core.logging import get_logger
from backend.app.db.models import RagDocument
from backend.app.ingestion.ocr import Transcriber
from backend.app.ingestion.reader import read_document
from backend.app.rag.chunking import chunk_document
from backend.app.rag.store import VectorStore
from backend.app.services.storage import save_upload

logger = get_logger(__name__)


@dataclass(frozen=True)
class IngestResult:
    document_id: str
    display_name: str
    chunk_count: int
    total_chunks_in_store: int


def ingest_into_knowledge_base(
    data: bytes,
    original_name: str,
    settings: Settings,
    session: Session,
    store: VectorStore,
    transcriber: Transcriber | None = None,
) -> IngestResult:
    """Validate, read, chunk and index a document.

    The file is kept on disk as well as indexed, so a citation can be traced
    back to the original rather than only to a chunk of its text.
    """
    stored = save_upload(data, original_name, settings.uploads_dir)
    extracted = read_document(stored.stored_path, stored.display_name, transcriber)

    record = RagDocument(
        display_name=stored.display_name,
        source_path=str(stored.stored_path),
    )
    session.add(record)
    session.flush()

    chunks = chunk_document(
        extracted,
        document_id=record.id,
        chunk_tokens=settings.chunk_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    written = store.add(chunks)
    record.chunk_count = written

    logger.info("Indexed %s into %s chunks", stored.display_name, written)
    return IngestResult(
        document_id=record.id,
        display_name=record.display_name,
        chunk_count=written,
        total_chunks_in_store=store.count(),
    )


def remove_from_knowledge_base(document_id: str, session: Session, store: VectorStore) -> bool:
    record = session.get(RagDocument, document_id)
    if record is None:
        return False

    store.delete_document(document_id)
    session.delete(record)
    return True
