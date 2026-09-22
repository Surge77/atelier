"""Database schema.

The shape here follows the core architectural idea: an artifact is a series of
immutable ``ContentPlan`` snapshots, and the DOCX/PPTX file on disk is just the
rendering of one of them. Nothing is updated in place except an artifact's
pointer to its newest version, so history and traceability are free.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    title: Mapped[str] = mapped_column(String(300), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    uploads: Mapped[list[Upload]] = relationship(back_populates="conversation")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="conversation")
    messages: Mapped[list[Message]] = relationship(back_populates="conversation")


class Message(Base):
    """A chat turn. Assistant turns keep the agent trace for replay."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    trace: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Upload(Base):
    """An uploaded template or source document, plus its analysis result."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), index=True, nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(500))
    suffix: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))  # docx | pptx | pdf | image | text
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    used_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    # One line describing what the analysis found, shown in the UI.
    summary: Mapped[str] = mapped_column(String(500), default="")
    # DocxTemplateProfile or PptxTemplateProfile, serialised.
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation | None] = relationship(back_populates="uploads")


class Artifact(Base):
    """A generated document or deck. Its content lives in its versions."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # document | deck
    title: Mapped[str] = mapped_column(String(300))
    # The upload this artifact borrows its styling from, if any.
    template_upload_id: Mapped[str | None] = mapped_column(ForeignKey("uploads.id"), nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="artifacts")
    versions: Mapped[list[ArtifactVersion]] = relationship(
        back_populates="artifact",
        order_by="ArtifactVersion.version_no",
        cascade="all, delete-orphan",
    )


class ArtifactVersion(Base):
    """One immutable snapshot: the plan, the ops that produced it, the file."""

    __tablename__ = "artifact_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    plan: Mapped[dict[str, Any]] = mapped_column(JSON)
    # PatchOps applied to the parent to reach this version. Empty for v1.
    ops: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    file_path: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    artifact: Mapped[Artifact] = relationship(back_populates="versions")


class RagDocument(Base):
    """Bookkeeping for the enterprise knowledge base, so the UI can list it."""

    __tablename__ = "rag_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    display_name: Mapped[str] = mapped_column(String(300))
    source_path: Mapped[str] = mapped_column(String(500), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
