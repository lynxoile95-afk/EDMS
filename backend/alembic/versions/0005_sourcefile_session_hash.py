"""Source-file deduplication becomes per upload session.

The old rule was ``UNIQUE(project_id, sha256)``: a project could hold a file's bytes
only once, and re-uploading the same PDF silently handed back the *old* row — which
belonged to the old session, leaving the new session empty. Committing that session
then failed with «هیچ فایلی برای پردازش وجود ندارد», although the upload had reported
success.

The same bytes still live once in storage (the new row points at the old
``storage_key``); what changes is that each session keeps its own row, so a retry is
processable. ``UNIQUE(session_id, sha256)`` keeps a double click on one session
idempotent.

Revision ID: 0005_sourcefile_session_hash
Revises: 0004_mdl_code_structure
Create Date: 2026-10-25 10:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_sourcefile_session_hash"
down_revision: str | None = "0004_mdl_code_structure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_files") as batch:
        batch.drop_constraint("uq_sourcefile_hash", type_="unique")
        batch.create_unique_constraint(
            "uq_sourcefile_session_hash", ["session_id", "sha256"]
        )


def downgrade() -> None:
    # Going back needs the per-project rule to hold again; rows created by the new
    # rule may violate it, so the caller has to clean those up first.
    with op.batch_alter_table("source_files") as batch:
        batch.drop_constraint("uq_sourcefile_session_hash", type_="unique")
        batch.create_unique_constraint("uq_sourcefile_hash", ["project_id", "sha256"])
