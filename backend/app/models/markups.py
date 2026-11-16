"""Offline PDF review markup persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONBType, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.core import Project, User
    from app.models.documents import DocumentRevision, SourceFile
    from app.models.transmittal import Transmittal


class MarkupSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reversible personal draft for a PDF transmittal attachment."""

    __tablename__ = "markup_sessions"
    __table_args__ = (
        UniqueConstraint(
            "transmittal_id",
            "source_attachment_id",
            "user_id",
            "status",
            name="uq_markup_session_draft_scope",
        ),
        Index("ix_markup_session_transmittal_source", "transmittal_id", "source_attachment_id"),
        Index("ix_markup_session_user_updated", "user_id", "updated_at"),
        Index("ix_markup_session_project_status", "project_id", "status"),
    )

    transmittal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transmittals.id"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_files.id"))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_revisions.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    #: UUID string of the legacy attachment in Transmittal.meta["attachments"].
    source_attachment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_code: Mapped[str | None] = mapped_column(String(255))
    revision_label: Mapped[str | None] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    original_storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft", nullable=False)
    draft_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_pdf_attachment_id: Mapped[str | None] = mapped_column(String(64))
    comment_sheet_attachment_id: Mapped[str | None] = mapped_column(String(64))
    generated_comment_ids: Mapped[list] = mapped_column(JSONBType, default=list, nullable=False)

    transmittal: Mapped["Transmittal"] = relationship(lazy="joined")
    project: Mapped["Project"] = relationship(lazy="joined")
    source_file: Mapped["SourceFile | None"] = relationship(lazy="joined")
    revision: Mapped["DocumentRevision | None"] = relationship(lazy="joined")
    user: Mapped["User"] = relationship(foreign_keys=[user_id], lazy="joined")
    annotations: Mapped[list["MarkupAnnotation"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarkupAnnotation.number",
    )


class MarkupAnnotation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A zoom-independent annotation stored as normalized geometry."""

    __tablename__ = "markup_annotations"
    __table_args__ = (
        UniqueConstraint("session_id", "number", name="uq_markup_annotation_number"),
        Index("ix_markup_annotation_session_page", "session_id", "page_no"),
        Index("ix_markup_annotation_session_status", "session_id", "reply_status"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("markup_sessions.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    w: Mapped[float] = mapped_column(Float, nullable=False)
    h: Mapped[float] = mapped_column(Float, nullable=False)
    points: Mapped[list] = mapped_column(JSONBType, default=list, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(16), default="#d64545", server_default="#d64545", nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(255))
    reply_status: Mapped[str] = mapped_column(String(16), default="open", server_default="open", nullable=False)
    reply_text: Mapped[str | None] = mapped_column(Text)
    replied_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    replied_by_name: Mapped[str | None] = mapped_column(String(255))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[MarkupSession] = relationship(back_populates="annotations")
    author: Mapped["User | None"] = relationship(foreign_keys=[author_id], lazy="joined")
    replied_by: Mapped["User | None"] = relationship(foreign_keys=[replied_by_id], lazy="joined")
