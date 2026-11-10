from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ActionRequired, CommentDisposition, PURPOSE_PATTERN
from app.schemas.common import ORMModel


MANUAL_DOC_STATUS_PATTERN = r"^(Commented|No Comment|Approve As Note)$"


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
    mdl_item_id: uuid.UUID | None = None
    action: str
    status: str
    doc_status: str | None = None
    due_date: date | None = None
    note: str | None = None
    doc_code: str | None = None
    title: str | None = None
    rev: str | None = None
    page_count: int | None = None
    discipline_code: str | None = None


class AttachmentOut(BaseModel):
    id: uuid.UUID
    line_id: uuid.UUID
    revision_id: uuid.UUID | None = None
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime
    uploaded_by_id: uuid.UUID | None = None


class TransmittalCreate(BaseModel):
    revision_ids: list[uuid.UUID] = Field(min_length=1)
    purpose: str = Field(default="IFR", pattern=PURPOSE_PATTERN)
    type: str = Field(default="internal", pattern=r"^(internal|external)$")
    remarks: str | None = None
    access_circle_id: uuid.UUID | None = None
    extra_recipient_ids: list[uuid.UUID] = []


class ReferralOut(ORMModel):
    id: uuid.UUID
    transmittal_id: uuid.UUID
    from_user_id: uuid.UUID | None = None
    from_user_name: str | None = None
    to_user_id: uuid.UUID
    to_user_name: str | None = None
    discipline_id: uuid.UUID | None = None
    discipline_code: str | None = None
    action: str
    note: str | None = None
    due_date: date | None = None
    status: str
    responded_at: datetime | None = None
    response_note: str | None = None
    created_at: datetime


class ForwardRequest(BaseModel):
    """Document control → project/engineering management."""

    to_user_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)
    due_days: int = Field(default=7, ge=1, le=90)


class ReferRequest(BaseModel):
    """Management → a discipline engineer (or a named user)."""

    discipline_id: uuid.UUID | None = None
    to_user_id: uuid.UUID | None = None
    action: ActionRequired = ActionRequired.REVIEW
    note: str | None = Field(default=None, max_length=2000)
    due_days: int = Field(default=14, ge=1, le=120)


class PersonnelReferralItem(BaseModel):
    """One selected project member and that person's individual referral data."""

    user_id: uuid.UUID
    action: ActionRequired = ActionRequired.REVIEW
    note: str | None = Field(default=None, max_length=2000)


class ReferPersonnelRequest(BaseModel):
    """Manual creator → active project members with per-person instructions.

    ``user_ids`` and the global ``note`` remain accepted for older clients. New
    clients should send ``people`` so each selected person can have a distinct
    referral operation and margin note.
    """

    people: list[PersonnelReferralItem] | None = None
    user_ids: list[uuid.UUID] | None = None
    note: str | None = Field(default=None, max_length=2000)
    due_days: int = Field(default=14, ge=1, le=120)


class CompleteRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class TransmittalOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    access_circle_id: uuid.UUID | None = None
    access_circle_name: str | None = None
    number: str | None = None
    type: str
    purpose: str
    status: str
    from_org: str | None = None
    to_org: str | None = None
    due_date: date | None = None
    issue_date: date | None = None
    issued_at: datetime | None = None
    created_by_id: uuid.UUID | None = None
    created_by_name: str | None = None
    issued_by_id: uuid.UUID | None = None
    issued_by_name: str | None = None
    final_approver_id: uuid.UUID | None = None
    final_approver_name: str | None = None
    manual_workflow: bool = False
    remarks: str | None = None
    meta: dict = {}
    stage: str = "draft"
    created_at: datetime
    line_count: int = 0
    recipient_count: int = 0
    acknowledged_count: int = 0
    # A compact first-line projection keeps the inbox useful without requiring
    # one detail request per transmittal row. Multi-line sheets still expose
    # their complete lines through TransmittalDetail.
    document_code: str | None = None
    document_title: str | None = None
    document_type: str | None = None
    line_action: str | None = None


class TransmittalDetail(TransmittalOut):
    lines: list[LineOut] = []
    recipients: list[RecipientOut] = []
    referrals: list[ReferralOut] = []
    attachments: list[AttachmentOut] = []
    pdf_url: str | None = None
    preview_number: str | None = None
    stage: str = "draft"
    stage_label: str | None = None


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
    related_comment_id: uuid.UUID | None = None
    discipline_id: uuid.UUID | None = None
    discipline_code: str | None = Field(default=None, max_length=32)
    page_no: int | None = Field(default=None, ge=1)
    from_page_no: int | None = Field(default=None, ge=1)
    to_page_no: int | None = Field(default=None, ge=1)
    bbox: list[float] = []
    disposition: CommentDisposition | None = None
    status_change_date: date | None = None
    personal_remarks: str | None = Field(default=None, max_length=4000)
    coordinator_date: date | None = None
    judgment: str = Field(default="draft", max_length=32)
    judgment_comments: str | None = Field(default=None, max_length=4000)
    remarks: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_page_range(self) -> "CommentCreate":
        if self.from_page_no is not None and self.to_page_no is not None and self.to_page_no < self.from_page_no:
            raise ValueError("صفحه پایان باید بزرگ‌تر یا مساوی صفحه شروع باشد.")
        return self


class CommentUpdate(BaseModel):
    text: str = Field(min_length=2, max_length=4000)
    code: str = Field(default="2", pattern=r"^[123]$")
    category: str | None = None
    related_comment_id: uuid.UUID | None = None
    discipline_id: uuid.UUID | None = None
    discipline_code: str | None = Field(default=None, max_length=32)
    page_no: int | None = Field(default=None, ge=1)
    from_page_no: int | None = Field(default=None, ge=1)
    to_page_no: int | None = Field(default=None, ge=1)
    disposition: CommentDisposition | None = None
    status_change_date: date | None = None
    personal_remarks: str | None = Field(default=None, max_length=4000)
    coordinator_date: date | None = None
    judgment: str = Field(default="draft", max_length=32)
    judgment_comments: str | None = Field(default=None, max_length=4000)
    remarks: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_page_range(self) -> "CommentUpdate":
        if self.from_page_no is not None and self.to_page_no is not None and self.to_page_no < self.from_page_no:
            raise ValueError("صفحه پایان باید بزرگ‌تر یا مساوی صفحه شروع باشد.")
        return self


class CommentRespond(BaseModel):
    response_text: str = Field(min_length=1, max_length=4000)


class CommentClose(BaseModel):
    """Body of `POST /comments/{id}/close`."""

    disposition: CommentDisposition = CommentDisposition.ACCEPTED
    note: str | None = None


class CommentAttachmentOut(ORMModel):
    id: uuid.UUID
    comment_id: uuid.UUID
    file_name: str
    file_size: int
    mime_type: str
    uploaded_by_id: uuid.UUID | None = None
    uploaded_by_name: str | None = None
    uploaded_at: datetime
    download_url: str


class CommentOut(ORMModel):
    id: uuid.UUID
    registration_id: str
    revision_id: uuid.UUID
    comment_list_id: uuid.UUID
    seq_no: int
    code: str
    category: str | None = None
    related_comment_id: uuid.UUID | None = None
    discipline_id: uuid.UUID | None = None
    discipline_code: str | None = None
    discipline_name: str | None = None
    text: str
    page_no: int | None = None
    from_page_no: int | None = None
    to_page_no: int | None = None
    bbox: list = []
    status: str
    disposition: str | None = None
    status_change_date: date | None = None
    personal_remarks: str | None = None
    coordinator_date: date | None = None
    judgment: str | None = None
    judgment_comments: str | None = None
    remarks: str | None = None
    response_text: str | None = None
    responded_at: datetime | None = None
    author_id: uuid.UUID | None = None
    author_name: str | None = None
    created_at: datetime
    revision_time: datetime | None = None
    attachments: list[CommentAttachmentOut] = []
    attachment_count: int = 0


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
