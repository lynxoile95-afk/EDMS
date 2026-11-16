"""Comment disposition — one nullable column on `comments`.

Additive and reversible: no back-fill, because a comment that was closed before
this migration has no known disposition and guessing one would be invented data.

Revision ID: 0002_comment_disposition
Revises: 0001_initial
Create Date: 2026-10-24 10:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_comment_disposition"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("comments") as batch:
        batch.add_column(sa.Column("disposition", sa.String(length=16), nullable=True))
    op.create_index("ix_comment_disposition", "comments", ["disposition"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_comment_disposition", table_name="comments")
    with op.batch_alter_table("comments") as batch:
        batch.drop_column("disposition")
