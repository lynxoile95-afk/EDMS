"""Transmittal registration: the draft rows built from uploaded PDFs.

A *registration* is a draft transmittal being filled in: the operator drops PDFs on
the screen, the first page of each goes to Gemini, and one row appears per file with
the extracted metadata. Nothing is written to the document register until the
operator presses save — that is what makes "re-extract", "remove row" and hand
editing safe.

The row keeps the model's answer verbatim (``extracted``) next to what the operator
finally confirmed, so an auditor can tell the two apart. That is the "store the
extracted JSON next to the PDF" requirement.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RegistrationRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One file = one row of the transmittal table."""

    __tablename__ = "registration_rows"
    __table_args__ = (
        UniqueConstraint("transmittal_id", "row_no", name="uq_registration_row_no"),
        Index("ix_registration_transmittal", "transmittal_id"),
        Index("ix_registration_file_hash", "project_id", "file_sha256"),
    )

    transmittal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transmittals.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="SET NULL")
    )
    row_no: Mapped[int] = mapped_column(Integer, default=1)
    filename: Mapped[str] = mapped_column(String(512), default="")
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer, default=0)

    # ---- the ten columns of the table ----
    doc_no: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(512))
    discipline_code: Mapped[str | None] = mapped_column(String(32))
    #: the label the project wants to see (``ME-Mechanical``), from the editable table
    discipline_label: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(16))
    poi: Mapped[str | None] = mapped_column(String(16))
    doc_status: Mapped[str | None] = mapped_column(String(64))
    doc_percent: Mapped[int] = mapped_column(Integer, default=0)
    mdl_status: Mapped[str | None] = mapped_column(String(64))
    confidentiality: Mapped[str] = mapped_column(String(24), default="Normal")

    # ---- extraction bookkeeping (audit) ----
    #: "ok" | "failed" | "manual" — only ``ok`` means a model answered
    extraction_status: Mapped[str] = mapped_column(String(16), default="manual")
    extraction_error: Mapped[str | None] = mapped_column(Text)
    extraction_model: Mapped[str | None] = mapped_column(String(120))
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=0)
    extracted_pages: Mapped[list] = mapped_column(default=list)
    #: the model's answer exactly as parsed (never edited afterwards)
    extracted: Mapped[dict] = mapped_column(default=dict)
    confidence: Mapped[dict] = mapped_column(default=dict)
    conflicts: Mapped[list] = mapped_column(default=list)
    warnings: Mapped[list] = mapped_column(default=list)

    # ---- review gate ----
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_at: Mapped[str | None] = mapped_column(String(40))

    # ---- result of validation against the register ----
    #: ok | new | supersede | duplicate | blocked
    validation_state: Mapped[str] = mapped_column(String(16), default="new")
    validation_messages: Mapped[list] = mapped_column(default=list)
    #: set once the row has been saved into the register
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="SET NULL")
    )
    saved: Mapped[bool] = mapped_column(Boolean, default=False)

    source_file: Mapped["SourceFile | None"] = relationship(lazy="joined")  # noqa: F821
