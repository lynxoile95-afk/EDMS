"""Store company codes used by the new transmittal number format.

Revision ID: 0010_project_company_codes
Revises: 0009_manual_transmittal_workflow
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_project_company_codes"
down_revision: str | None = "0009_manual_transmittal_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable columns are intentional: projects created before this migration
    # remain readable and can be completed from the project editor later.
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("contractor_code", sa.String(length=2), nullable=True))
        batch_op.add_column(sa.Column("parent_company_code", sa.String(length=2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_column("parent_company_code")
        batch_op.drop_column("contractor_code")
