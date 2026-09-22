"""The chat endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.core.errors import NotFoundError
from backend.app.db.models import Conversation, Message
from backend.app.db.session import get_session
from backend.app.llm.factory import get_provider
from backend.app.rag.factory import get_store
from backend.app.search.factory import get_search_provider
from backend.app.services.chat import ChatOutcome, handle_turn

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ConversationView(BaseModel):
    id: str
    title: str


class ArtifactRef(BaseModel):
    id: str
    kind: str
    title: str
    version: int
    download_url: str
    validation_passed: bool
    citation_coverage: float


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    intent: str
    agents_involved: list[str]
    artifacts: list[ArtifactRef]
    citations: list[dict[str, Any]]
    trace: list[dict[str, Any]]


class MessageView(BaseModel):
    role: str
    content: str
    trace: list[dict[str, Any]]


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> ChatResponse:
    """Run one turn of the multi-agent conversation.

    The full agent trace is returned with the reply rather than streamed. A
    turn is a handful of sequential model calls, so a single response keeps the
    client simple and the trace is displayed as a timeline either way.
    """
    outcome = handle_turn(
        message=request.message,
        conversation_id=request.conversation_id,
        settings=settings,
        session=session,
        provider=get_provider(),
        search_provider=get_search_provider(),
        store=get_store(),
    )

    return ChatResponse(
        conversation_id=outcome.conversation_id,
        reply=outcome.result.reply,
        intent=outcome.result.plan.intent,
        agents_involved=outcome.result.plan.agents_involved(),
        artifacts=[
            ArtifactRef(
                id=item.artifact.id,
                kind=item.artifact.kind,
                title=item.artifact.title,
                version=item.version.version_no,
                download_url=(
                    f"/api/artifacts/{item.artifact.id}/versions/"
                    f"{item.version.version_no}/download"
                ),
                validation_passed=item.validation.passed,
                citation_coverage=item.validation.citation_coverage,
            )
            for item in outcome.result.produced
        ],
        citations=_citations_of(outcome),
        trace=[event.model_dump(mode="json") for event in outcome.events],
    )


@router.post("/conversations", response_model=ConversationView, status_code=201)
def create_conversation(session: Session = Depends(get_session)) -> ConversationView:
    """Start an empty conversation.

    A client that creates one up front can attach every upload to it, which
    removes the window where a template belongs to no conversation and could be
    claimed by the wrong one.
    """
    conversation = Conversation()
    session.add(conversation)
    session.flush()
    return ConversationView(id=conversation.id, title=conversation.title)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageView])
def list_messages(
    conversation_id: str, session: Session = Depends(get_session)
) -> list[MessageView]:
    if session.get(Conversation, conversation_id) is None:
        raise NotFoundError(f"No conversation {conversation_id!r}.")

    rows = (
        session.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
        .all()
    )
    return [MessageView(role=r.role, content=r.content, trace=r.trace or []) for r in rows]


def _citations_of(outcome: ChatOutcome) -> list[dict[str, Any]]:
    """Citations from the newest produced artifact, de-duplicated by id."""
    if not outcome.result.produced:
        return []

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in outcome.result.produced:
        for citation in item.version.citations:
            if citation["id"] not in seen:
                seen.add(citation["id"])
                merged.append(citation)
    return merged
