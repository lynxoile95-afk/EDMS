from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class FieldEvidence(BaseModel):
    field_name: str
    raw_value: str | None = None
    normalized_value: str | None = None
    confidence: float = 0.0
    source: str = "rule"
    bbox: list[float] | None = None
    page_no: int = 1
    note: str | None = None


class IssueOut(ORMModel):
    id: uuid.UUID
    rule_code: str
    severity: str
    field_name: str | None = None
    message: str
    message_fa: str | None = None
    status: str
    context: dict = {}


class MatchCandidateOut(BaseModel):
    mdl_item_id: str
    doc_code: str
    title: str
    discipline_code: str | None = None
    score: float
    method: str
    reasons: list[str] = []


class ExtractionResultOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    sheet_id: uuid.UUID
    source_file_id: uuid.UUID
    session_id: uuid.UUID | None = None
    doc_code: str | None = None
    rev: str | None = None
    title: str | None = None
    discipline_code: str | None = None
    unit_code: str | None = None
    doc_status: str | None = None
    doc_date: date | None = None
    sheet_no: int | None = None
    sheet_count: int | None = None
    overall_confidence: float
    field_confidence: dict = {}
    field_sources: dict = {}
    decision: str
    review_status: str
    match_score: float = 0.0
    match_method: str | None = None
    matched_mdl_item_id: uuid.UUID | None = None
    human_corrections: dict = {}
    review_note: str | None = None
    created_at: datetime
    issues: list[IssueOut] = []


class ExtractionResultDetail(ExtractionResultOut):
    payload: dict = {}
    candidates: list[MatchCandidateOut] = []
    thumbnail_url: str | None = None
    title_block_url: str | None = None
    file_name: str | None = None
    file_url: str | None = None
    summary_fa: str | None = None
    mdl_title: str | None = None
    mdl_current_rev: str | None = None


class ReviewAction(BaseModel):
    """DCC decision on an extraction result."""

    action: str = Field(pattern=r"^(register|quarantine|reject|ignore)$")
    mdl_item_id: uuid.UUID | None = None
    rev: str | None = None
    title: str | None = None
    discipline_code: str | None = None
    unit_code: str | None = None
    notes: str | None = None
    corrections: dict | None = None
    create_transmittal: bool = False
    purpose: str | None = None
    transmittal_type: str = Field(default="internal", pattern=r"^(internal|external)$")
    recipient_ids: list[uuid.UUID] | None = None
    override_issues: bool = False


class ReviewOutcome(BaseModel):
    result_id: uuid.UUID
    action: str
    decision: str
    revision_id: uuid.UUID | None = None
    transmittal_ids: list[uuid.UUID] = []
    message: str


class BulkReviewRequest(BaseModel):
    result_ids: list[uuid.UUID]
    action: str
    notes: str | None = None
