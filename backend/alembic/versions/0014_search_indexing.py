"""Add opt-in PDF search chunks and durable indexing jobs.

Revision ID: 0014_search_indexing
Revises: 0013_ai_credentials

This migration is additive. PostgreSQL extensions are enabled with
``IF NOT EXISTS`` and are intentionally never removed by downgrade because
other applications may use them. The existing ``source_files.id`` is UUID, so
``document_chunks.file_id`` is UUID as well; changing it to BIGINT would make a
foreign key to the existing table impossible.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover - migration image installs pgvector
    Vector = None  # type: ignore[assignment]

revision: str = "0014_search_indexing"
down_revision: str | None = "0013_ai_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    postgres = _is_postgres()
    if postgres:
        # Never pair these with DROP EXTENSION in downgrade. They may predate
        # EDMS and are shared database capabilities, not owned objects.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        if Vector is None:  # pragma: no cover
            raise RuntimeError("pgvector Python package is required for PostgreSQL migrations")
        embedding_type = Vector(768)
        search_vector_type = postgresql.TSVECTOR()
    else:
        # Preview/Test SQLite remains healthy and deliberately has no vector or
        # PostgreSQL-specific index. The search service uses bounded LIKE there.
        embedding_type = sa.JSON()
        search_vector_type = sa.Text()

    chunk_columns = [
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("index_version", sa.String(length=64), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False, server_default="native"),
        sa.Column("char_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("char_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("embedding", embedding_type, nullable=True),
    ]
    if postgres:
        chunk_columns.append(
            sa.Column(
                "search_vector",
                search_vector_type,
                sa.Computed("to_tsvector('simple', coalesce(content, ''))", persisted=True),
                nullable=True,
            )
        )
    else:
        chunk_columns.append(sa.Column("search_vector", search_vector_type, nullable=True))
    chunk_columns.extend(
        [
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["file_id"], ["source_files.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "file_id", "page_no", "chunk_index", "index_version",
                name="uq_document_chunk_version",
            ),
        ]
    )
    op.create_table("document_chunks", *chunk_columns)
    op.create_index("ix_document_chunk_file_page", "document_chunks", ["file_id", "page_no"])
    op.create_index(
        "ix_document_chunk_project_active", "document_chunks", ["project_id", "is_active"]
    )
    op.create_index(
        "ix_document_chunk_revision_active", "document_chunks", ["revision_id", "is_active"]
    )
    if postgres:
        op.create_index(
            "ix_document_chunk_search_vector",
            "document_chunks",
            ["search_vector"],
            postgresql_using="gin",
        )
        op.create_index(
            "ix_document_chunk_content_trgm",
            "document_chunks",
            ["content"],
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        )
        op.create_index(
            "ix_document_chunk_embedding_hnsw",
            "document_chunks",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )

    op.create_table(
        "index_jobs",
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("index_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cursor_page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_pages", sa.JSON(), nullable=False, server_default="[]"),
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
    op.create_index("ix_index_job_file_status", "index_jobs", ["file_id", "status"])
    op.create_index("ix_index_job_project_status", "index_jobs", ["project_id", "status"])


def downgrade() -> None:
    # Only objects created above are removed. In particular, extensions are
    # intentionally retained for other database consumers.
    postgres = _is_postgres()
    op.drop_index("ix_index_job_project_status", table_name="index_jobs")
    op.drop_index("ix_index_job_file_status", table_name="index_jobs")
    op.drop_table("index_jobs")
    if postgres:
        op.drop_index("ix_document_chunk_embedding_hnsw", table_name="document_chunks")
        op.drop_index("ix_document_chunk_content_trgm", table_name="document_chunks")
        op.drop_index("ix_document_chunk_search_vector", table_name="document_chunks")
    op.drop_index("ix_document_chunk_revision_active", table_name="document_chunks")
    op.drop_index("ix_document_chunk_project_active", table_name="document_chunks")
    op.drop_index("ix_document_chunk_file_page", table_name="document_chunks")
    op.drop_table("document_chunks")
