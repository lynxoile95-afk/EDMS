"""Add the Persian file-QA index and conversation state additively.

Revision ID: 0015_persian_rag_v2
Revises: 0014_search_indexing

The legacy document_chunks table and its vector(768) column are not altered.
PostgreSQL-only vector/GIN/HNSW objects are dialect guarded; SQLite keeps JSON
vectors and the service uses a bounded keyword fallback.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover - the production image installs pgvector
    Vector = None  # type: ignore[assignment]

revision: str = "0015_persian_rag_v2"
down_revision: str | None = "0014_search_indexing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    postgres = _is_postgres()
    if postgres:
        if Vector is None:  # pragma: no cover
            raise RuntimeError("pgvector Python package is required for PostgreSQL migrations")
        embedding_type = Vector(384)
    else:
        embedding_type = sa.JSON()

    op.create_table(
        "document_chunks_v2",
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("index_version", sa.String(length=64), nullable=False, server_default="persian-rag-v2"),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("text_original", sa.Text(), nullable=False),
        sa.Column("text_normalized", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("source_document", sa.String(length=512), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=True),
        sa.Column("embedding", embedding_type, nullable=True),
        sa.Column("embedding_model", sa.String(length=255), nullable=False, server_default="intfloat/multilingual-e5-small"),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False, server_default="384"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["source_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id", "revision_id", "page_no", "chunk_index", "index_version",
            name="uq_document_chunk_v2_version",
        ),
    )
    op.create_index("ix_document_chunk_v2_file_active", "document_chunks_v2", ["file_id", "is_active"])
    op.create_index("ix_document_chunk_v2_project_active", "document_chunks_v2", ["project_id", "is_active"])
    op.create_index("ix_document_chunk_v2_revision_active", "document_chunks_v2", ["revision_id", "is_active"])
    if postgres:
        op.create_index(
            "ix_document_chunk_v2_text_normalized_trgm",
            "document_chunks_v2",
            ["text_normalized"],
            postgresql_using="gin",
            postgresql_ops={"text_normalized": "gin_trgm_ops"},
        )
        op.create_index(
            "ix_document_chunk_v2_embedding_hnsw",
            "document_chunks_v2",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )

    op.create_table(
        "rag_index_jobs",
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("index_version", sa.String(length=64), nullable=False, server_default="persian-rag-v2"),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False, server_default="384"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("cursor_chunk", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=True),
        sa.Column("force_reindex", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["source_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_index_job_file_status", "rag_index_jobs", ["file_id", "status"])
    op.create_index("ix_rag_index_job_project_status", "rag_index_jobs", ["project_id", "status"])

    op.create_table(
        "file_qa_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default="پرسش از فایل‌ها"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_file_qa_session_user_updated", "file_qa_sessions", ["user_id", "updated_at"])

    op.create_table(
        "file_qa_messages",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["file_qa_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_file_qa_message_session_created", "file_qa_messages", ["session_id", "created_at"])


def downgrade() -> None:
    """Remove only objects introduced by this migration; keep extensions/data before it."""

    op.drop_index("ix_file_qa_message_session_created", table_name="file_qa_messages")
    op.drop_table("file_qa_messages")
    op.drop_index("ix_file_qa_session_user_updated", table_name="file_qa_sessions")
    op.drop_table("file_qa_sessions")
    op.drop_index("ix_rag_index_job_project_status", table_name="rag_index_jobs")
    op.drop_index("ix_rag_index_job_file_status", table_name="rag_index_jobs")
    op.drop_table("rag_index_jobs")
    postgres = _is_postgres()
    if postgres:
        op.drop_index("ix_document_chunk_v2_embedding_hnsw", table_name="document_chunks_v2")
        op.drop_index("ix_document_chunk_v2_text_normalized_trgm", table_name="document_chunks_v2")
    op.drop_index("ix_document_chunk_v2_revision_active", table_name="document_chunks_v2")
    op.drop_index("ix_document_chunk_v2_project_active", table_name="document_chunks_v2")
    op.drop_index("ix_document_chunk_v2_file_active", table_name="document_chunks_v2")
    op.drop_table("document_chunks_v2")
