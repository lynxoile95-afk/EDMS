"""Add the coding-structure columns to the MDL.

Mirrors the attached sample MDL (``2102-20-ED-GN-MDL-9001-A8.pdf``): every row carries
a phase (EB/ED), a serial number, a BASIC/DETAIL flag, a CLASS, a P.O.I and a remark.
All nullable — existing rows keep working and are back-filled from their code the next
time they are imported or edited.

Revision ID: 0004_mdl_code_structure
Revises: 0003_review_route
Create Date: 2026-10-24 12:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_mdl_code_structure"
down_revision: str | None = "0003_review_route"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("phase", sa.String(length=8)),
    ("serial_no", sa.String(length=8)),
    ("category", sa.String(length=64)),
    ("doc_class", sa.String(length=4)),
    ("basic_detail", sa.String(length=4)),
    ("poi", sa.String(length=8)),
    ("remark", sa.String(length=255)),
)


def upgrade() -> None:
    with op.batch_alter_table("mdl_items") as batch:
        for name, type_ in COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))
    op.create_index("ix_mdl_phase", "mdl_items", ["phase"])


def downgrade() -> None:
    op.drop_index("ix_mdl_phase", table_name="mdl_items")
    with op.batch_alter_table("mdl_items") as batch:
        for name, _ in reversed(COLUMNS):
            batch.drop_column(name)
