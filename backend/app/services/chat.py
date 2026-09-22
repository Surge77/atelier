"""Assembles a turn and records it.

Sits between the HTTP route and the orchestrator so the whole conversational
path can be exercised in tests without going through FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.db.models import Artifact, Conversation, Message, Upload
from backend.app.llm.base import LLMProvider
from backend.app.orchestration.events import AgentEvent, EventRecorder
from backend.app.orchestration.orchestrator import TurnResult, run_turn
from backend.app.orchestration.run_state import RunState, templates_from_uploads
from backend.app.rag.store import VectorStore
from backend.app.search.provider import SearchProvider

MAX_TITLE_CHARS = 80


@dataclass
class ChatOutcome:
    conversation_id: str
    result: TurnResult
    events: list[AgentEvent]


def handle_turn(
    message: str,
    conversation_id: str | None,
    settings: Settings,
    session: Session,
    provider: LLMProvider,
    search_provider: SearchProvider,
    store: VectorStore,
    sink=None,  # EventSink
) -> ChatOutcome:
    """Run one user turn end to end and persist both sides of it."""
    conversation = _ensure_conversation(session, conversation_id, message)
    _adopt_orphan_uploads(session, conversation.id)
    session.add(Message(conversation_id=conversation.id, role="user", content=message))

    recorder = EventRecorder(sink)
    state = _build_state(
        message, conversation, settings, session, provider, search_provider, store
    )

    result = run_turn(state, recorder)

    session.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=result.reply,
            trace=recorder.as_dicts(),
        )
    )
    session.flush()

    return ChatOutcome(conversation.id, result, recorder.events)


def _ensure_conversation(
    session: Session, conversation_id: str | None, first_message: str
) -> Conversation:
    if conversation_id:
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None:
            return conversation

    conversation = Conversation(title=first_message[:MAX_TITLE_CHARS] or "New conversation")
    session.add(conversation)
    session.flush()
    return conversation


def _adopt_orphan_uploads(session: Session, conversation_id: str) -> None:
    """Attach uploads made before any conversation existed.

    People upload a template and *then* start talking, and the UI does the same
    on a first turn, so those uploads carry no conversation id yet. Without
    this they would be invisible to the very turn they were uploaded for.
    """
    orphans = session.query(Upload).filter(Upload.conversation_id.is_(None)).all()
    for upload in orphans:
        upload.conversation_id = conversation_id

    if orphans:
        session.flush()


def _build_state(
    message: str,
    conversation: Conversation,
    settings: Settings,
    session: Session,
    provider: LLMProvider,
    search_provider: SearchProvider,
    store: VectorStore,
) -> RunState:
    uploads = (
        session.query(Upload)
        .filter(Upload.conversation_id == conversation.id)
        .order_by(Upload.created_at.desc())
        .all()
    )
    artifacts = (
        session.query(Artifact)
        .filter(Artifact.conversation_id == conversation.id)
        .order_by(Artifact.created_at)
        .all()
    )
    docx_template, pptx_template = templates_from_uploads(uploads)

    return RunState(
        conversation_id=conversation.id,
        user_message=message,
        settings=settings,
        session=session,
        provider=provider,
        search_provider=search_provider,
        store=store,
        docx_template=docx_template,
        pptx_template=pptx_template,
        existing_artifacts=artifacts,
    )
