"""MDL, uploads, extraction artefacts, validation issues and document revisions."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AVStatus,
    Decision,
    ExtractionSource,
    ExtractionStatus,
    FileStatus,
    IssueStatus,
    MDLImportStatus,
    PurposeOfIssue,
    ReviewStatus,
    RevisionStatus,
    Severity,
    SubmissionMode,
    UploadStatus,
)


class MDLImportBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mdl_import_batches"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[MDLImportStatus] = mapped_column(String(24), default=MDLImportStatus.VALIDATING)
    mapping: Mapped[dict] = mapped_column(default=dict)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[list] = mapped_column(default=list)  # [{row, column, severity, message}]
    imported_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class MDLItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row of the Master Document List — the authoritative register."""

    __tablename__ = "mdl_items"
    __table_args__ = (
        UniqueConstraint("project_id", "doc_code_norm", name="uq_mdl_code"),
        Index("ix_mdl_project_discipline", "project_id", "discipline_id"),
        Index("ix_mdl_project_status", "project_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mdl_import_batches.id", ondelete="SET NULL"))
    doc_code: Mapped[str] = mapped_column(String(120), nullable=False)
    #: upper-case, dash/digit normalised — every lookup uses this column
    doc_code_norm: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    title_norm: Mapped[str] = mapped_column(String(512), default="", server_default="")
    discipline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("disciplines.id", ondelete="SET NULL"))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id", ondelete="SET NULL"))
    doc_type: Mapped[str | None] = mapped_column(String(64))
    current_rev: Mapped[str | None] = mapped_column(String(16))
    rev_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str | None] = mapped_column(String(64))
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    planned_issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    weight: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    page_count: Mapped[int | None] = mapped_column(Integer)
    aliases: Mapped[list] = mapped_column(default=list)  # legacy / contractor codes
    attributes: Mapped[dict] = mapped_column(default=dict)  # any extra MDL columns
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    source_row: Mapped[int | None] = mapped_column(Integer)

    revisions: Mapped[list["DocumentRevision"]] = relationship(
        back_populates="mdl_item", cascade="all, delete-orphan", lazy="selectin"
    )
    discipline: Mapped["Discipline | None"] = relationship(lazy="joined")  # noqa: F821
    unit: Mapped["Unit | None"] = relationship(lazy="joined")  # noqa: F821
    responsible: Mapped["User | None"] = relationship(lazy="joined")  # noqa: F821


class UploadSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "upload_sessions"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    purpose: Mapped[PurposeOfIssue] = mapped_column(String(16), default=PurposeOfIssue.IFR)
    mode: Mapped[SubmissionMode] = mapped_column(String(24), default=SubmissionMode.NEW_DOCUMENT)
    transmittal_type: Mapped[str] = mapped_column(String(16), default="internal")
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[UploadStatus] = mapped_column(String(24), default=UploadStatus.CREATED, index=True)
    stats: Mapped[dict] = mapped_column(default=dict)

    files: Mapped[list["SourceFile"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", lazy="selectin"
    )


class SourceFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_sourcefile_hash"),
        Index("ix_sourcefile_session", "session_id"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("upload_sessions.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    is_encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    av_status: Mapped[AVStatus] = mapped_column(String(16), default=AVStatus.PENDING)
    status: Mapped[FileStatus] = mapped_column(String(24), default=FileStatus.UPLOADED, index=True)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    error_message: Mapped[str | None] = mapped_column(Text)

    session: Mapped[UploadSession] = relationship(back_populates="files")
    sheets: Mapped[list["Sheet"]] = relationship(
        back_populates="source_file", cascade="all, delete-orphan", lazy="selectin"
    )


class Sheet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One physical page of a source PDF."""

    __tablename__ = "sheets"
    __table_args__ = (
        UniqueConstraint("source_file_id", "page_no", name="uq_sheet_page"),
    )

    source_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    width_pt: Mapped[float] = mapped_column(Float, default=0)
    height_pt: Mapped[float] = mapped_column(Float, default=0)
    dpi_estimate: Mapped[float] = mapped_column(Float, default=0)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(1024))
    title_block_key: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(24), default="pending")

    source_file: Mapped[SourceFile] = relationship(back_populates="sheets")


class ExtractionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution of the extraction pipeline over one sheet."""

    __tablename__ = "extraction_runs"

    sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("title_block_templates.id", ondelete="SET NULL"))
    status: Mapped[ExtractionStatus] = mapped_column(String(24), default=ExtractionStatus.QUEUED, index=True)
    methods_used: Mapped[list] = mapped_column(default=list)  # ["text_layer","ocr","vlm"]
    models: Mapped[dict] = mapped_column(default=dict)  # {"ocr":"tesseract:5.3","vlm":"qwen2.5-vl"}
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_text_key: Mapped[str | None] = mapped_column(String(1024))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionField(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-field evidence: raw value, normalised value, confidence and bbox."""

    __tablename__ = "extraction_fields"
    __table_args__ = (Index("ix_exfield_run_name", "run_id", "field_name"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_runs.id", ondelete="CASCADE"))
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[ExtractionSource] = mapped_column(String(16), default=ExtractionSource.RULE)
    evidence: Mapped[dict] = mapped_column(default=dict)  # {"bbox":[..], "page":1, "method":...}


class ExtractionResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Aggregated, reviewable result for one sheet."""

    __tablename__ = "extraction_results"
    __table_args__ = (
        Index("ix_extraction_project_decision", "project_id", "decision"),
        Index("ix_extraction_doccode", "doc_code_norm"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extraction_runs.id", ondelete="SET NULL"))
    source_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("upload_sessions.id", ondelete="SET NULL"))

    doc_code: Mapped[str | None] = mapped_column(String(120))
    doc_code_norm: Mapped[str | None] = mapped_column(String(120))
    rev: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(512))
    discipline_code: Mapped[str | None] = mapped_column(String(32))
    unit_code: Mapped[str | None] = mapped_column(String(32))
    doc_status: Mapped[str | None] = mapped_column(String(64))
    doc_date: Mapped[date | None] = mapped_column(Date)
    sheet_no: Mapped[int | None] = mapped_column(Integer)
    sheet_count: Mapped[int | None] = mapped_column(Integer)

    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    field_confidence: Mapped[dict] = mapped_column(default=dict)   # field -> 0..1
    field_sources: Mapped[dict] = mapped_column(default=dict)      # field -> method
    decision: Mapped[Decision] = mapped_column(String(8), default=Decision.PENDING, index=True)
    payload: Mapped[dict] = mapped_column(default=dict)            # full JSON dump
    raw_text: Mapped[str | None] = mapped_column(Text)
    title_block_key: Mapped[str | None] = mapped_column(String(1024))

    matched_mdl_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mdl_items.id", ondelete="SET NULL"))
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    match_method: Mapped[str | None] = mapped_column(String(32))

    review_status: Mapped[ReviewStatus] = mapped_column(String(16), default=ReviewStatus.PENDING, index=True)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    human_corrections: Mapped[dict] = mapped_column(default=dict)  # field -> {"model":..,"human":..}
    duplicate_of_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_files.id", ondelete="SET NULL"))

    issues: Mapped[list["ValidationIssue"]] = relationship(
        back_populates="result", cascade="all, delete-orphan", lazy="selectin"
    )


class ValidationIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "validation_issues"
    __table_args__ = (Index("ix_issue_result", "result_id"),)

    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_results.id", ondelete="CASCADE"))
    rule_code: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[Severity] = mapped_column(String(16), default=Severity.WARNING)
    field_name: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    message_fa: Mapped[str | None] = mapped_column(Text)
    status: Mapped[IssueStatus] = mapped_column(String(16), default=IssueStatus.OPEN)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(default=dict)

    result: Mapped[ExtractionResult] = relationship(back_populates="issues")


class DocumentRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only register of registered revisions."""

    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("mdl_item_id", "rev", name="uq_revision_per_item"),
        UniqueConstraint("project_id", "file_hash", name="uq_revision_file_hash"),
        Index("ix_revision_project_current", "project_id", "is_current"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mdl_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdl_items.id", ondelete="CASCADE"), index=True)
    rev: Mapped[str] = mapped_column(String(16), nullable=False)
    rev_seq: Mapped[int] = mapped_column(Integer, default=1)
    title_as_drawn: Mapped[str | None] = mapped_column(String(512))
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_files.id", ondelete="SET NULL"))
    extraction_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extraction_results.id", ondelete="SET NULL")
    )
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[RevisionStatus] = mapped_column(String(16), default=RevisionStatus.REGISTERED, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    registered_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_revisions.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)

    mdl_item: Mapped[MDLItem] = relationship(back_populates="revisions", lazy="joined")
    comments: Mapped[list["Comment"]] = relationship(  # noqa: F821  (defined in transmittal.py)
        back_populates="revision", cascade="all, delete-orphan", lazy="selectin"
    )
