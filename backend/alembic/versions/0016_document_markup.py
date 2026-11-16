"""Add personal PDF document-review markup sessions.

Revision ID: 0016_document_markup
Revises: 0015_persian_rag_v2

Only the two markup tables and their indexes are owned by this migration.  The
source PDF and all legacy transmittal/document tables remain untouched.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_document_markup"
down_revision: str | None = "0015_persian_rag_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "markup_sessions",
        sa.Column("transmittal_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_file_id", sa.Uuid(), nullable=True),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_attachment_id", sa.String(length=64), nullable=False),
        sa.Column("document_code", sa.String(length=255), nullable=True),
        sa.Column("revision_label", sa.String(length=64), nullable=True),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("original_storage_key", sa.String(length=1024), nullable=False),
        sa.Column("original_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("draft_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_pdf_attachment_id", sa.String(length=64), nullable=True),
        sa.Column("comment_sheet_attachment_id", sa.String(length=64), nullable=True),
        sa.Column(
            "generated_comment_ids",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["transmittal_id"], ["transmittals.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transmittal_id",
            "source_attachment_id",
            "user_id",
            "status",
            name="uq_markup_session_draft_scope",
        ),
    )
    op.create_index(
        "ix_markup_session_transmittal_source",
        "markup_sessions",
        ["transmittal_id", "source_attachment_id"],
    )
    op.create_index("ix_markup_session_user_updated", "markup_sessions", ["user_id", "updated_at"])
    op.create_index("ix_markup_session_project_status", "markup_sessions", ["project_id", "status"])
    op.create_table(
        "markup_annotations",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("w", sa.Float(), nullable=False),
        sa.Column("h", sa.Float(), nullable=False),
        sa.Column(
            "points",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#d64545"),
        sa.Column("author_name", sa.String(length=255), nullable=True),
        sa.Column("reply_status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("reply_text", sa.Text(), nullable=True),
        sa.Column("replied_by_id", sa.Uuid(), nullable=True),
        sa.Column("replied_by_name", sa.String(length=255), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["markup_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["replied_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "number", name="uq_markup_annotation_number"),
    )
    op.create_index("ix_markup_annotation_session_page", "markup_annotations", ["session_id", "page_no"])
    op.create_index("ix_markup_annotation_session_status", "markup_annotations", ["session_id", "reply_status"])


def downgrade() -> None:
    """Remove only the objects introduced above."""
    op.drop_index("ix_markup_annotation_session_status", table_name="markup_annotations")
    op.drop_index("ix_markup_annotation_session_page", table_name="markup_annotations")
    op.drop_table("markup_annotations")

    op.drop_index("ix_markup_session_project_status", table_name="markup_sessions")
    op.drop_index("ix_markup_session_user_updated", table_name="markup_sessions")
    op.drop_index("ix_markup_session_transmittal_source", table_name="markup_sessions")
    op.drop_table("markup_sessions")
