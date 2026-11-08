"""Allow free-text project-member role labels.

Revision ID: 0008_project_member_free_text_role
Revises: 0007_user_identity_access_circles
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_project_member_free_text_role"
down_revision: str | None = "0007_user_identity_access_circles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("project_members", schema=None) as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(length=32),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("project_members", schema=None) as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(length=255),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
