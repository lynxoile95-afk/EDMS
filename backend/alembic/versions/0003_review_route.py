"""Review route: transmittal stage + referral hops.

Additive: one column on `transmittals` and one new table. Existing sheets keep
stage `draft`, which the API treats as "not yet routed".

Revision ID: 0003_review_route
Revises: 0002_comment_disposition
Create Date: 2026-10-24 11:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_review_route"
down_revision: str | None = "0002_comment_disposition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transmittals") as batch:
        batch.add_column(
            sa.Column("stage", sa.String(length=16), nullable=False, server_default="draft")
        )
    op.create_index("ix_transmittals_stage", "transmittals", ["stage"], unique=False)

    op.create_table(
        "transmittal_referrals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transmittal_id", sa.Uuid(), nullable=False),
        sa.Column("from_user_id", sa.Uuid(), nullable=True),
        sa.Column("to_user_id", sa.Uuid(), nullable=False),
        sa.Column("discipline_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=24), nullable=False, server_default="review"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["transmittal_id"], ["transmittals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["to_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["discipline_id"], ["disciplines.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transmittal_referrals_transmittal_id", "transmittal_referrals", ["transmittal_id"])
    op.create_index("ix_transmittal_referrals_to_user_id", "transmittal_referrals", ["to_user_id"])
    op.create_index("ix_transmittal_referrals_status", "transmittal_referrals", ["status"])
    op.create_index(
        "ix_referral_transmittal_status", "transmittal_referrals", ["transmittal_id", "status"]
    )
    op.create_index("ix_referral_to_status", "transmittal_referrals", ["to_user_id", "status"])


def downgrade() -> None:
    op.drop_table("transmittal_referrals")
    op.drop_index("ix_transmittals_stage", table_name="transmittals")
    with op.batch_alter_table("transmittals") as batch:
        batch.drop_column("stage")
