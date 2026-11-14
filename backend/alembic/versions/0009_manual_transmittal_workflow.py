"""Manual transmittal fields and document-level status.

Revision ID: 0009_manual_transmittal_workflow
Revises: 0008_project_member_free_text_role
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_manual_transmittal_workflow"
down_revision: str | None = "0008_project_member_free_text_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transmittals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("final_approver_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("issue_date", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("manual_workflow", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_foreign_key(
            "fk_transmittals_final_approver_id_users",
            "users",
            ["final_approver_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_transmittals_manual_workflow", ["manual_workflow"])

    with op.batch_alter_table("transmittal_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("doc_status", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transmittal_lines", schema=None) as batch_op:
        batch_op.drop_column("doc_status")

    with op.batch_alter_table("transmittals", schema=None) as batch_op:
        batch_op.drop_index("ix_transmittals_manual_workflow")
        batch_op.drop_constraint("fk_transmittals_final_approver_id_users", type_="foreignkey")
        batch_op.drop_column("manual_workflow")
        batch_op.drop_column("issue_date")
        batch_op.drop_column("final_approver_id")
