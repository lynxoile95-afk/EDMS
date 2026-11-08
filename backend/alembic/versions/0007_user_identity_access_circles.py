"""User identity fields and project access circles.

Revision ID: 0007_user_identity_access_circles
Revises: 0006_transmittal_registration
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_user_identity_access_circles"
down_revision: str | None = "0006_transmittal_registration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing installations may already have users, therefore the columns remain
    # nullable at database level. The API requires and validates them for every new
    # account; legacy accounts can be completed from the user-management screen.
    op.add_column("users", sa.Column("national_id", sa.String(length=10), nullable=True))
    op.add_column("users", sa.Column("mobile", sa.String(length=32), nullable=True))
    op.create_index("ix_users_national_id", "users", ["national_id"], unique=True)
    op.create_index("ix_users_mobile", "users", ["mobile"], unique=True)

    op.create_table(
        "access_circles",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_access_circle_project_code"),
    )
    op.create_index(
        "ix_access_circle_project_active", "access_circles", ["project_id", "is_active"]
    )
    op.create_index("ix_access_circles_project_id", "access_circles", ["project_id"])

    op.create_table(
        "access_circle_members",
        sa.Column("circle_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("can_access_external", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["circle_id"], ["access_circles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("circle_id", "user_id", name="uq_access_circle_member"),
    )
    op.create_index(
        "ix_access_circle_member_user", "access_circle_members", ["user_id", "circle_id"]
    )

    with op.batch_alter_table("transmittals") as batch_op:
        batch_op.add_column(sa.Column("access_circle_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_transmittals_access_circle_id_access_circles",
            "access_circles",
            ["access_circle_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_transmittals_access_circle_id", ["access_circle_id"])


def downgrade() -> None:
    with op.batch_alter_table("transmittals") as batch_op:
        batch_op.drop_index("ix_transmittals_access_circle_id")
        batch_op.drop_constraint(
            "fk_transmittals_access_circle_id_access_circles", type_="foreignkey"
        )
        batch_op.drop_column("access_circle_id")

    op.drop_index("ix_access_circle_member_user", table_name="access_circle_members")
    op.drop_table("access_circle_members")
    op.drop_index("ix_access_circle_project_active", table_name="access_circles")
    op.drop_index("ix_access_circles_project_id", table_name="access_circles")
    op.drop_table("access_circles")

    op.drop_index("ix_users_mobile", table_name="users")
    op.drop_index("ix_users_national_id", table_name="users")
    op.drop_column("users", "mobile")
    op.drop_column("users", "national_id")
