"""Add the full comment-registration fields and authenticated attachments.

Revision ID: 0011_comment_registration
Revises: 0010_project_company_codes
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_comment_registration"
down_revision: str | None = "0010_project_company_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("related_comment_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("discipline_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("from_page_no", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("to_page_no", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("status_change_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("personal_remarks", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("coordinator_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("judgment", sa.String(length=32), nullable=True, server_default="draft"))
        batch_op.add_column(sa.Column("judgment_comments", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("remarks", sa.Text(), nullable=True))
        batch_op.create_unique_constraint("uq_comment_list_seq", ["comment_list_id", "seq_no"])
        batch_op.create_index("ix_comments_related_comment_id", ["related_comment_id"], unique=False)
        batch_op.create_index("ix_comments_discipline_id", ["discipline_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_comments_related_comment_id_comments",
            "comments",
            ["related_comment_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_comments_discipline_id_disciplines",
            "disciplines",
            ["discipline_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "comment_attachments",
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_path"),
    )
    op.create_index("ix_comment_attachments_comment_id", "comment_attachments", ["comment_id"], unique=False)
    op.create_index(
        "ix_comment_attachment_comment",
        "comment_attachments",
        ["comment_id", "uploaded_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_comment_attachment_comment", table_name="comment_attachments")
    op.drop_index("ix_comment_attachments_comment_id", table_name="comment_attachments")
    op.drop_table("comment_attachments")

    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.drop_constraint("uq_comment_list_seq", type_="unique")
        batch_op.drop_constraint("fk_comments_discipline_id_disciplines", type_="foreignkey")
        batch_op.drop_constraint("fk_comments_related_comment_id_comments", type_="foreignkey")
        batch_op.drop_index("ix_comments_discipline_id")
        batch_op.drop_index("ix_comments_related_comment_id")
        batch_op.drop_column("remarks")
        batch_op.drop_column("judgment_comments")
        batch_op.drop_column("judgment")
        batch_op.drop_column("coordinator_date")
        batch_op.drop_column("personal_remarks")
        batch_op.drop_column("status_change_date")
        batch_op.drop_column("to_page_no")
        batch_op.drop_column("from_page_no")
        batch_op.drop_column("discipline_id")
        batch_op.drop_column("related_comment_id")
