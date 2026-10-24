from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import PURPOSE_PATTERN
from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class UploadSessionCreate(BaseModel):
    purpose: str = Field(default="IFR", pattern=PURPOSE_PATTERN)
    mode: str = Field(default="new_document", pattern=r"^(new_document|revision|response_to_comment)$")
    transmittal_type: str = Field(default="internal", pattern=r"^(internal|external)$")
    note: str | None = None


class SourceFileOut(ORMModel):
    id: uuid.UUID
    original_filename: str
    size_bytes: int
    sha256: str
    page_count: int
    is_scanned: bool
    is_encrypted: bool
    av_status: str
    status: str
    error_message: str | None = None
    created_at: datetime


class SheetOut(ORMModel):
    id: uuid.UUID
    page_no: int
    width_pt: float
    height_pt: float
    has_text_layer: bool
    status: str


class UploadSessionOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    purpose: str
    mode: str
    transmittal_type: str
    status: str
    note: str | None = None
    created_at: datetime
    files: list[SourceFileOut] = []


class UploadSessionDetail(UploadSessionOut):
    results: list[dict] = []


class CommitResponse(BaseModel):
    session_id: uuid.UUID
    status: str
    queued_files: int
    task_id: str | None = None
    message: str
