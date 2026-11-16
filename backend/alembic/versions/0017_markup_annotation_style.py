"""Store user-selectable PDF markup line width.

Revision ID: 0017_markup_annotation_style
Revises: 0016_document_markup

Only the new appearance table belongs to this migration. Existing markup,
transmittal, document, and source-file tables are intentionally untouched.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_markup_annotation_style"
down_revision: str | None = "0016_document_markup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "markup_annotation_styles",
        sa.Column("annotation_id", sa.Uuid(), nullable=False),
        sa.Column("stroke_width", sa.Float(), nullable=False, server_default="2.5"),
        sa.ForeignKeyConstraint(
            ["annotation_id"],
            ["markup_annotations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("annotation_id"),
    )


def downgrade() -> None:
    """Remove only the appearance table introduced by this migration."""
    op.drop_table("markup_annotation_styles")
