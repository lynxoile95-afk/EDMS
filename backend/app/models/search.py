"""Additive models for legacy search and the versioned Persian file-QA index."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    FetchedValue,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONBType, TimestampMixin, UUIDPrimaryKeyMixin

try:  # pgvector is optional in the SQLite Preview environment.
    from pgvector.sqlalchemy import Vector  # type: ignore
except ImportError:  # pragma: no cover - production installs pgvector
    from sqlalchemy.types import TypeDecorator

    class Vector(TypeDecorator):  # type: ignore[no-redef]
        """JSON fallback used only when the optional pgvector wheel is absent."""

        impl = JSONBType
        cache_ok = True

        def __init__(self, dimensions: int, *args, **kwargs):
            self.dimensions = dimensions
            super().__init__(*args, **kwargs)


class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One page-local text chunk in the original 768-dimensional index."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "file_id", "page_no", "chunk_index", "index_version",
            name="uq_document_chunk_version",
        ),
        Index("ix_document_chunk_file_page", "file_id", "page_no"),
        Index("ix_document_chunk_project_active", "project_id", "is_active"),
        Index("ix_document_chunk_revision_active", "revision_id", "is_active"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="SET NULL"), nullable=True
    )
    index_version: Mapped[str] = mapped_column(String(64), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="native")
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # PG uses vector(768); SQLite uses JSON through the model variant.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768).with_variant(JSON(), "sqlite"), nullable=True
    )
    search_vector: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=FetchedValue()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class IndexJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable, resumable indexing work item for the original PDF path."""

    __tablename__ = "index_jobs"
    __table_args__ = (
        Index("ix_index_job_file_status", "file_id", "status"),
        Index("ix_index_job_project_status", "project_id", "status"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="SET NULL"), nullable=True
    )
    index_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_pages: Mapped[list] = mapped_column(JSONBType, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    force_reindex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunkV2(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The additive 384-dimensional Persian RAG representation.

    ``text_original`` is the only display/snippet value.  ``text_normalized``
    is retrieval-only, and the legacy ``document_chunks`` rows/768 vectors are
    never updated by this model.
    """

    __tablename__ = "document_chunks_v2"
    __table_args__ = (
        UniqueConstraint(
            "file_id", "revision_id", "page_no", "chunk_index", "index_version",
            name="uq_document_chunk_v2_version",
        ),
        Index("ix_document_chunk_v2_file_active", "file_id", "is_active"),
        Index("ix_document_chunk_v2_project_active", "project_id", "is_active"),
        Index("ix_document_chunk_v2_revision_active", "revision_id", "is_active"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="SET NULL"), nullable=True
    )
    index_version: Mapped[str] = mapped_column(String(64), nullable=False, default="persian-rag-v2")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_original: Mapped[str] = mapped_column(Text, nullable=False)
    text_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    source_document: Mapped[str] = mapped_column(String(512), nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(384).with_variant(JSON(), "sqlite"), nullable=True
    )
    embedding_model: Mapped[str] = mapped_column(
        String(255), nullable=False, default="intfloat/multilingual-e5-small"
    )
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=384)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# Explicit aliases make the additive table easy to discover for integrations.
RAGChunk = DocumentChunkV2


class RAGIndexJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Resumable/throttled v2 backfill job, independent of legacy IndexJob."""

    __tablename__ = "rag_index_jobs"
    __table_args__ = (
        Index("ix_rag_index_job_file_status", "file_id", "status"),
        Index("ix_rag_index_job_project_status", "project_id", "status"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="SET NULL"), nullable=True
    )
    index_version: Mapped[str] = mapped_column(String(64), nullable=False, default="persian-rag-v2")
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=384)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    cursor_chunk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    force_reindex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileQASession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's authenticated file-QA conversation."""

    __tablename__ = "file_qa_sessions"
    __table_args__ = (Index("ix_file_qa_session_user_updated", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), default="پرسش از فایل‌ها", server_default="پرسش از فایل‌ها")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FileQAMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted conversation turn; source JSON contains metadata, not keys."""

    __tablename__ = "file_qa_messages"
    __table_args__ = (Index("ix_file_qa_message_session_created", "session_id", "created_at"),)

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("file_qa_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSONBType, nullable=False, default=list)
