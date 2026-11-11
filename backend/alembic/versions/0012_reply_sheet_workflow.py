"""Persist Reply Sheet forms and AFC finalization fields.

Revision ID: 0012_reply_sheet_workflow
Revises: 0011_comment_registration
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_reply_sheet_workflow"
down_revision: str | None = "0011_comment_registration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transmittal_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("doc_percent", sa.Integer(), nullable=True))

    op.create_table(
        "reply_sheets",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("transmittal_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=96), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("replied_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_to_id", sa.Uuid(), nullable=True),
        sa.Column("completed_by_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("judgment", sa.String(length=32), nullable=True),
        sa.Column("doc_status", sa.String(length=64), nullable=True),
        sa.Column("doc_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_comment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transmittal_id"], ["transmittals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replied_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["completed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
        sa.UniqueConstraint("transmittal_id", "sequence_no", name="uq_reply_sheet_sequence"),
    )
    op.create_index("ix_reply_sheet_project_id", "reply_sheets", ["project_id"], unique=False)
    op.create_index("ix_reply_sheet_transmittal_id", "reply_sheets", ["transmittal_id"], unique=False)
    op.create_index("ix_reply_sheet_number", "reply_sheets", ["number"], unique=False)
    op.create_index("ix_reply_sheet_transmittal_status", "reply_sheets", ["transmittal_id", "status"], unique=False)

    op.create_table(
        "reply_sheet_items",
        sa.Column("reply_sheet_id", sa.Uuid(), nullable=False),
        sa.Column("transmittal_line_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=48), nullable=False, server_default="pending"),
        sa.Column("judgment", sa.String(length=32), nullable=True),
        sa.Column("owner_comments", sa.Text(), nullable=True),
        sa.Column("provider_clarification", sa.Text(), nullable=True),
        sa.Column("assign_user_id", sa.Uuid(), nullable=True),
        sa.Column("doc_status", sa.String(length=64), nullable=True),
        sa.Column("doc_percent", sa.Integer(), nullable=True),
        sa.Column("excluded_reason", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["reply_sheet_id"], ["reply_sheets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transmittal_line_id"], ["transmittal_lines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assign_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reply_sheet_item_sheet", "reply_sheet_items", ["reply_sheet_id"], unique=False)
    op.create_index("ix_reply_sheet_item_comment", "reply_sheet_items", ["comment_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reply_sheet_item_comment", table_name="reply_sheet_items")
    op.drop_index("ix_reply_sheet_item_sheet", table_name="reply_sheet_items")
    op.drop_table("reply_sheet_items")
    op.drop_index("ix_reply_sheet_transmittal_status", table_name="reply_sheets")
    op.drop_index("ix_reply_sheet_number", table_name="reply_sheets")
    op.drop_index("ix_reply_sheet_transmittal_id", table_name="reply_sheets")
    op.drop_index("ix_reply_sheet_project_id", table_name="reply_sheets")
    op.drop_table("reply_sheets")
    with op.batch_alter_table("transmittal_lines", schema=None) as batch_op:
        batch_op.drop_column("doc_percent")
