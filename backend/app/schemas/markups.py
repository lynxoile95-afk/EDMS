"""API contracts for the offline-capable PDF review markup workflow."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


MarkupKind = Literal[
    "sticky_note",
    "highlight",
    "underline",
    "strikethrough",
    "rectangle",
    "ellipse",
    "revision_cloud",
    "arrow",
    "free_text",
    "callout",
    "freehand",
]
ReplyStatus = Literal["open", "closed", "accepted", "rejected", "resolved"]


class MarkupPoint(BaseModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]


class MarkupAnnotationInput(BaseModel):
    id: uuid.UUID | None = None
    kind: MarkupKind
    page_no: Annotated[int, Field(ge=1)]
    points: list[MarkupPoint] = Field(min_length=1, max_length=1000)
    text: str | None = Field(default=None, max_length=10000)
    color: str = Field(default="#d64545", pattern=r"^#[0-9A-Fa-f]{6}$")
    # Stored in the feature-owned appearance table so existing markup columns
    # and the source document schema remain untouched.
    stroke_width: float = Field(default=2.5, ge=1, le=10)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class MarkupAnnotationsSave(BaseModel):
    annotations: list[MarkupAnnotationInput] = Field(default_factory=list, max_length=5000)
    expected_version: int | None = Field(default=None, ge=0)


class MarkupReply(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    status: ReplyStatus = "resolved"

    @field_validator("text")
    @classmethod
    def normalize_reply(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("متن پاسخ نمی‌تواند خالی باشد.")
        return value


class MarkupCreate(BaseModel):
    transmittal_id: uuid.UUID
    file_id: uuid.UUID


class MarkupAnnotationOut(BaseModel):
    id: uuid.UUID
    number: int
    kind: MarkupKind
    page_no: int
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    w: Annotated[float, Field(ge=0, le=1)]
    h: Annotated[float, Field(ge=0, le=1)]
    points: list[MarkupPoint]
    text: str | None = None
    color: str
    stroke_width: float = Field(default=2.5, ge=1, le=10)
    author_id: uuid.UUID | None = None
    author_name: str | None = None
    created_at: datetime
    updated_at: datetime
    reply_status: ReplyStatus
    reply_text: str | None = None
    replied_by_id: uuid.UUID | None = None
    replied_by_name: str | None = None
    replied_at: datetime | None = None


class MarkupOutputOut(BaseModel):
    attachment_id: uuid.UUID
    filename: str
    kind: Literal["pdf", "sheet"]
    download_url: str
    size_bytes: int = 0
    content_type: str = "application/pdf"


class MarkupSessionOut(BaseModel):
    id: uuid.UUID
    transmittal_id: uuid.UUID
    project_id: uuid.UUID
    source_file_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    file_id: uuid.UUID
    document_code: str | None = None
    revision_label: str | None = None
    original_filename: str
    original_sha256: str
    page_count: int
    status: Literal["draft", "submitted"]
    draft_version: int
    last_saved_at: datetime | None = None
    submitted_at: datetime | None = None
    author_id: uuid.UUID
    author_name: str | None = None
    can_edit: bool = False
    can_reply: bool = False
    submitted_pdf_attachment_id: uuid.UUID | None = None
    comment_sheet_attachment_id: uuid.UUID | None = None
    outputs: list[MarkupOutputOut] = Field(default_factory=list)
    annotations: list[MarkupAnnotationOut] = Field(default_factory=list)


class MarkupSubmitOut(BaseModel):
    session: MarkupSessionOut
    outputs: list[MarkupOutputOut]
    annotation_count: int
