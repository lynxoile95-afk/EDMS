"""Transmittal registration rows.

One row per uploaded PDF on the «ثبت و بارگذاری مدرک» screen: the ten columns of the
transmittal table, the model's answer kept verbatim for audit, its per-field
confidence and conflicts, and the review gate.

Revision ID: 0006_transmittal_registration
Revises: 0005_sourcefile_session_hash
Create Date: 2026-10-25 11:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_transmittal_registration"
down_revision: str | None = "0005_sourcefile_session_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registration_rows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("transmittal_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_file_id", sa.Uuid(), nullable=True),
        sa.Column("row_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("filename", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("doc_no", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("discipline_code", sa.String(length=32), nullable=True),
        sa.Column("discipline_label", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=16), nullable=True),
        sa.Column("poi", sa.String(length=16), nullable=True),
        sa.Column("doc_status", sa.String(length=64), nullable=True),
        sa.Column("doc_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mdl_status", sa.String(length=64), nullable=True),
        sa.Column("confidentiality", sa.String(length=24), nullable=False, server_default="Normal"),
        sa.Column("extraction_status", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column("extraction_model", sa.String(length=120), nullable=True),
        sa.Column("extraction_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_pages", sa.JSON(), nullable=False),
        sa.Column("extracted", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.JSON(), nullable=False),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("validated_at", sa.String(length=40), nullable=True),
        sa.Column("validation_state", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column("validation_messages", sa.JSON(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["transmittal_id"], ["transmittals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("transmittal_id", "row_no", name="uq_registration_row_no"),
    )
    op.create_index("ix_registration_rows_transmittal_id", "registration_rows", ["transmittal_id"])
    op.create_index("ix_registration_rows_project_id", "registration_rows", ["project_id"])
    op.create_index("ix_registration_transmittal", "registration_rows", ["transmittal_id"])
    op.create_index("ix_registration_file_hash", "registration_rows", ["project_id", "file_sha256"])


def downgrade() -> None:
    op.drop_index("ix_registration_file_hash", table_name="registration_rows")
    op.drop_index("ix_registration_transmittal", table_name="registration_rows")
    op.drop_index("ix_registration_rows_project_id", table_name="registration_rows")
    op.drop_index("ix_registration_rows_transmittal_id", table_name="registration_rows")
    op.drop_table("registration_rows")
