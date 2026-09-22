"""Upload handling: validate, store, analyse, persist.

Sits between the route and the agents so the route stays thin and the whole
accept-and-analyse path can be tested without HTTP.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.agents.analysis_agents import (
    AnalysisResult,
    analyse_document,
    analyse_presentation,
)
from backend.app.config import Settings
from backend.app.core.logging import get_logger
from backend.app.db.models import Upload
from backend.app.ingestion.ocr import Transcriber
from backend.app.ingestion.reader import kind_for_suffix
from backend.app.llm.base import LLMProvider
from backend.app.services.storage import save_upload

logger = get_logger(__name__)

# Enough of the source text to drive generation without bloating the database.
MAX_STORED_TEXT_CHARS = 40_000


def ingest_upload(
    data: bytes,
    original_name: str,
    settings: Settings,
    session: Session,
    conversation_id: str | None = None,
    provider: LLMProvider | None = None,
    transcriber: Transcriber | None = None,
) -> tuple[Upload, AnalysisResult]:
    """Accept a file, analyse it, and record both.

    Validation happens before anything is written, so a rejected upload leaves
    nothing on disk.
    """
    stored = save_upload(data, original_name, settings.uploads_dir)
    kind = kind_for_suffix(stored.suffix)

    analysis = _analyse(stored.stored_path, stored.display_name, kind, provider, transcriber)

    upload = Upload(
        conversation_id=conversation_id,
        display_name=stored.display_name,
        stored_path=str(stored.stored_path),
        suffix=stored.suffix,
        kind=kind,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        page_count=analysis.extracted.page_count,
        used_ocr=analysis.extracted.used_ocr,
        summary=analysis.summary,
        profile=analysis.profile.model_dump(mode="json"),
        extracted_text=analysis.extracted.text[:MAX_STORED_TEXT_CHARS],
    )
    session.add(upload)
    session.flush()

    logger.info("Ingested %s (%s): %s", stored.display_name, kind, analysis.summary)
    return upload, analysis


def _analyse(
    path: Path,
    display_name: str,
    kind: str,
    provider: LLMProvider | None,
    transcriber: Transcriber | None,
) -> AnalysisResult:
    if kind == "pptx":
        return analyse_presentation(path, display_name, provider)
    return analyse_document(path, display_name, provider, transcriber)
