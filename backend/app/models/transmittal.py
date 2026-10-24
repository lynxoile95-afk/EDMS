"""DDM, transmittals, comments, notifications, numbering and audit."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AcknowledgementStatus,
    ActionRequired,
    CommentCode,
    CommentStatus,
    NotificationChannel,
    NotificationStatus,
    PurposeOfIssue,
    TransmittalLineStatus,
    TransmittalStatus,
    TransmittalType,
)


class DDMRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Document Distribution Matrix rule.

    A rule matches when each of its non-null criteria equals the document's
    value.  The most specific rule (most non-null criteria, then highest
    priority) wins.
    """

    __tablename__ = "ddm_rules"
    __table_args__ = (Index("ix_ddm_project_active", "project_id", "is_active"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    discipline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("disciplines.id", ondelete="CASCADE"))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"))
    doc_type: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[PurposeOfIssue | None] = mapped_column(String(16))
    default_action: Mapped[ActionRequired] = mapped_column(String(24), default=ActionRequired.REVIEW)
    due_days: Mapped[int] = mapped_column(Integer, default=14, server_default="14")
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipients: Mapped[list["DDMRuleRecipient"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", lazy="selectin"
    )


class DDMRuleRecipient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ddm_rule_recipients"
    __table_args__ = (UniqueConstraint("rule_id", "user_id", name="uq_ddm_recipient"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ddm_rules.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[ActionRequired] = mapped_column(String(24), default=ActionRequired.REVIEW)

    rule: Mapped[DDMRule] = relationship(back_populates="recipients")


class NumberSequence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Row-locked counters so concurrent issues never collide."""

    __tablename__ = "number_sequences"
    __table_args__ = (UniqueConstraint("project_id", "scope_key", name="uq_sequence_scope"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    next_value: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Transmittal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transmittals"
    __table_args__ = (
        Index("ix_transmittal_project_status", "project_id", "status"),
        Index("ix_transmittal_project_created", "project_id", "created_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    seq: Mapped[int | None] = mapped_column(Integer)
    type: Mapped[TransmittalType] = mapped_column(String(16), default=TransmittalType.INTERNAL)
    purpose: Mapped[PurposeOfIssue] = mapped_column(String(16), nullable=False)
    status: Mapped[TransmittalStatus] = mapped_column(String(24), default=TransmittalStatus.DRAFT, index=True)
    from_org: Mapped[str | None] = mapped_column(String(255))
    to_org: Mapped[str | None] = mapped_column(String(255))
    sender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[date | None] = mapped_column(Date)
    remarks: Mapped[str | None] = mapped_column(Text)
    pdf_key: Mapped[str | None] = mapped_column(String(1024))
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    excel_key: Mapped[str | None] = mapped_column(String(1024))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("upload_sessions.id", ondelete="SET NULL"))
    previous_transmittal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transmittals.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    recipient_snapshot: Mapped[list] = mapped_column(default=list)
    meta: Mapped[dict] = mapped_column(default=dict)
    #: stage of the review route (see TransmittalStage); status stays the sheet's own state
    stage: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft", index=True)

    lines: Mapped[list["TransmittalLine"]] = relationship(
        back_populates="transmittal", cascade="all, delete-orphan", lazy="selectin",
        order_by="TransmittalLine.line_no",
    )
    recipients: Mapped[list["TransmittalRecipient"]] = relationship(
        back_populates="transmittal", cascade="all, delete-orphan", lazy="selectin"
    )
    referrals: Mapped[list["TransmittalReferral"]] = relationship(
        back_populates="transmittal", cascade="all, delete-orphan", lazy="selectin",
        order_by="TransmittalReferral.created_at",
    )


class TransmittalLine(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transmittal_lines"
    __table_args__ = (
        UniqueConstraint("transmittal_id", "revision_id", name="uq_transmittal_revision"),
        Index("ix_line_revision", "revision_id"),
    )

    transmittal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transmittals.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_revisions.id", ondelete="RESTRICT"))
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    action: Mapped[ActionRequired] = mapped_column(String(24), default=ActionRequired.REVIEW)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[TransmittalLineStatus] = mapped_column(String(16), default=TransmittalLineStatus.DRAFT)
    note: Mapped[str | None] = mapped_column(Text)
    discipline_code: Mapped[str | None] = mapped_column(String(32))

    transmittal: Mapped[Transmittal] = relationship(back_populates="lines")
    revision: Mapped["DocumentRevision"] = relationship(lazy="joined")  # noqa: F821


class TransmittalRecipient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transmittal_recipients"
    __table_args__ = (
        UniqueConstraint("transmittal_id", "user_id", name="uq_transmittal_user"),
        Index("ix_trecipient_user", "user_id", "ack_status"),
    )

    transmittal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transmittals.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[ActionRequired] = mapped_column(String(24), default=ActionRequired.REVIEW)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_status: Mapped[AcknowledgementStatus] = mapped_column(
        String(16), default=AcknowledgementStatus.PENDING, index=True
    )
    ack_note: Mapped[str | None] = mapped_column(Text)

    transmittal: Mapped[Transmittal] = relationship(back_populates="recipients")
    user: Mapped["User"] = relationship(lazy="joined")  # noqa: F821


class CommentList(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "comment_lists"
    __table_args__ = (UniqueConstraint("revision_id", "transmittal_id", name="uq_commentlist_scope"),)

    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_revisions.id", ondelete="CASCADE"), index=True)
    transmittal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transmittals.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="open")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="comment_list", cascade="all, delete-orphan", lazy="selectin",
        order_by="Comment.seq_no",
    )


class Comment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "comments"
    __table_args__ = (Index("ix_comment_revision", "revision_id", "status"),)

    comment_list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comment_lists.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_revisions.id", ondelete="CASCADE"))
    sheet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sheets.id", ondelete="SET NULL"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    seq_no: Mapped[int] = mapped_column(Integer, default=1)
    code: Mapped[CommentCode] = mapped_column(String(4), default=CommentCode.CODE2)
    category: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list] = mapped_column(default=list)
    status: Mapped[CommentStatus] = mapped_column(String(16), default=CommentStatus.OPEN)
    # Set when the coordinator closes the comment; NULL while it is still open.
    disposition: Mapped[str | None] = mapped_column(String(16))
    response_text: Mapped[str | None] = mapped_column(Text)
    responded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    comment_list: Mapped[CommentList] = relationship(back_populates="comments")
    revision: Mapped["DocumentRevision"] = relationship(back_populates="comments")  # noqa: F821
    author: Mapped["User"] = relationship(foreign_keys=[author_id], lazy="joined")  # noqa: F821


class TransmittalReferral(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One hop of the review route: who handed the sheet to whom, and why.

    This is the table behind «گردش مدرک» in the reference system: sender,
    receiver, action, receive date, answer date. A referral is created by
    ``POST /transmittals/{id}/forward`` (document control → management) and by
    ``POST /transmittals/{id}/refer`` (management → a discipline engineer).
    """

    __tablename__ = "transmittal_referrals"
    __table_args__ = (
        Index("ix_referral_transmittal_status", "transmittal_id", "status"),
        Index("ix_referral_to_status", "to_user_id", "status"),
    )

    transmittal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transmittals.id", ondelete="CASCADE"), index=True
    )
    from_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    to_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    discipline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("disciplines.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(24), default="review")
    note: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_note: Mapped[str | None] = mapped_column(Text)

    transmittal: Mapped[Transmittal] = relationship(back_populates="referrals")
    from_user: Mapped["User | None"] = relationship(foreign_keys=[from_user_id], lazy="joined")  # noqa: F821
    to_user: Mapped["User"] = relationship(foreign_keys=[to_user_id], lazy="joined")  # noqa: F821
    discipline: Mapped["Discipline | None"] = relationship(lazy="joined")  # noqa: F821


class NotificationLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_logs"
    __table_args__ = (Index("ix_notify_user_status", "user_id", "status"),)

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[NotificationChannel] = mapped_column(String(16), default=NotificationChannel.IN_APP)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(512))
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[NotificationStatus] = mapped_column(String(16), default=NotificationStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    dedupe_key: Mapped[str | None] = mapped_column(String(160), index=True)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Immutable trail. Never updated, never deleted (revoke UPDATE/DELETE in DB)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_project_created", "project_id", "created_at"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(default=dict)
    after: Mapped[dict | None] = mapped_column(default=dict)
    meta: Mapped[dict] = mapped_column(default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
