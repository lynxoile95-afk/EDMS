from __future__ import annotations

import uuid
from datetime import date, datetime

from app.models.enums import CommentDisposition, PURPOSE_PATTERN
from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class RecipientOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    action: str
    ack_status: str
    acknowledged_at: datetime | None = None
    full_name: str | None = None
    email: str | None = None


class LineOut(ORMModel):
    id: uuid.UUID
    line_no: int
    revision_id: uuid.UUID
    action: str
    status: str
    due_date: date | None = None
    note: str | None = None
    doc_code: str | None = None
    title: str | None = None
    rev: str | None = None
    page_count: int | None = None
    discipline_code: str | None = None


class TransmittalCreate(BaseModel):
    revision_ids: list[uuid.UUID] = Field(min_length=1)
    purpose: str = Field(default="IFR", pattern=PURPOSE_PATTERN)
    type: str = Field(default="internal", pattern=r"^(internal|external)$")
    remarks: str | None = None
    extra_recipient_ids: list[uuid.UUID] = []


class TransmittalOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    number: str | None = None
    type: str
    purpose: str
    status: str
    from_org: str | None = None
    to_org: str | None = None
    due_date: date | None = None
    issued_at: datetime | None = None
    remarks: str | None = None
    meta: dict = {}
    created_at: datetime
    line_count: int = 0
    recipient_count: int = 0
    acknowledged_count: int = 0


class TransmittalDetail(TransmittalOut):
    lines: list[LineOut] = []
    recipients: list[RecipientOut] = []
    pdf_url: str | None = None
    preview_number: str | None = None


class AcknowledgeRequest(BaseModel):
    note: str | None = None


class CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class IssueRequest(BaseModel):
    remarks: str | None = None
    notify: bool = True


class DDMRuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    discipline_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    doc_type: str | None = None
    purpose: str | None = Field(default=None, pattern=PURPOSE_PATTERN)
    default_action: str = Field(default="review", pattern=r"^(review|approve|info|for_construction)$")
    due_days: int = Field(default=14, ge=1, le=365)
    priority: int = 100
    is_active: bool = True
    recipients: list[dict] = []


class DDMRuleOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    discipline_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    doc_type: str | None = None
    purpose: str | None = None
    default_action: str
    due_days: int
    priority: int
    is_active: bool
    recipients: list[RecipientOut] = []


class DDMSimulateRequest(BaseModel):
    discipline_code: str | None = None
    unit_code: str | None = None
    doc_type: str | None = None
    purpose: str = "IFR"


class DDMSimulateOut(BaseModel):
    matched: bool
    rule_name: str | None = None
    due_days: int
    explanation: str
    recipients: list[dict] = []


class CommentCreate(BaseModel):
    revision_id: uuid.UUID
    text: str = Field(min_length=2, max_length=4000)
    code: str = Field(default="2", pattern=r"^[123]$")
    category: str | None = None
    page_no: int | None = None
    bbox: list[float] = []


class CommentRespond(BaseModel):
    response_text: str = Field(min_length=1, max_length=4000)


class CommentClose(BaseModel):
    """Body of `POST /comments/{id}/close`."""

    disposition: CommentDisposition = CommentDisposition.ACCEPTED
    note: str | None = None


class CommentOut(ORMModel):
    id: uuid.UUID
    revision_id: uuid.UUID
    comment_list_id: uuid.UUID
    seq_no: int
    code: str
    category: str | None = None
    text: str
    page_no: int | None = None
    bbox: list = []
    status: str
    disposition: str | None = None
    response_text: str | None = None
    responded_at: datetime | None = None
    author_name: str | None = None
    created_at: datetime


class CommentListOut(ORMModel):
    id: uuid.UUID
    revision_id: uuid.UUID
    transmittal_id: uuid.UUID | None = None
    status: str
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    comments: list[CommentOut] = []


class NotificationOut(ORMModel):
    id: uuid.UUID
    channel: str
    template: str
    subject: str | None = None
    body: str | None = None
    status: str
    created_at: datetime
    read_at: datetime | None = None
    entity_type: str | None = None
    entity_id: str | None = None


class DashboardOut(BaseModel):
    project_id: uuid.UUID
    documents_total: int
    documents_issued: int
    revisions_total: int
    review_queue: int
    quarantined: int
    transmittals_total: int
    transmittals_open: int
    auto_accept_rate: float
    issues_by_rule: dict = {}
    uploads_last_7_days: int
    overdue_documents: int
