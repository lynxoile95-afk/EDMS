from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class MDLPreviewRequest(BaseModel):
    sheet_name: str | None = None


class MDLPreviewOut(BaseModel):
    sheets: list[str]
    active_sheet: str | None
    header_row: int
    headers: list[str]
    suggested_mapping: dict[str, int]
    sample_rows: list[list] = []


class MDLImportRequest(BaseModel):
    """Sent as a JSON string in the `config` form field of the multipart request."""

    sheet_name: str | None = None
    header_row: int | None = None
    mapping: dict[str, int]
    auto_create_lookups: bool = True
    dry_run: bool = False


class MDLImportEntry(BaseModel):
    row: int
    severity: str
    column: str | None = None
    message: str


class MDLImportReport(BaseModel):
    status: str
    batch_id: str | None = None
    total_rows: int = 0
    imported: int = 0
    updated: int = 0
    errors: int = 0
    warnings: int = 0
    entries: list[MDLImportEntry] = []


class MDLItemOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    doc_code: str
    title: str
    doc_type: str | None = None
    current_rev: str | None = None
    rev_seq: int = 0
    status: str | None = None
    weight: float = 1.0
    page_count: int | None = None
    due_date: date | None = None
    planned_issue_date: date | None = None
    is_active: bool
    discipline_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    discipline_code: str | None = None
    unit_code: str | None = None
    revision_count: int = 0


class MDLItemCreate(BaseModel):
    """One manually typed MDL row (the Excel import is the other way in)."""

    doc_code: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=2, max_length=500)
    doc_type: str | None = Field(default=None, max_length=32)
    current_rev: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=32)
    discipline_code: str | None = Field(default=None, max_length=16)
    unit_code: str | None = Field(default=None, max_length=32)
    weight: float = Field(default=1.0, ge=0, le=1000)
    page_count: int | None = Field(default=None, ge=0, le=10000)


class MDLItemUpdate(BaseModel):
    title: str | None = None
    doc_type: str | None = None
    current_rev: str | None = None
    status: str | None = None
    weight: float | None = None
    discipline_code: str | None = None
    unit_code: str | None = None
    aliases: list[str] | None = None
    reason: str | None = None


class MDLImportBatchOut(ORMModel):
    id: uuid.UUID
    filename: str
    status: str
    row_count: int
    imported_count: int
    error_count: int
    warning_count: int
    created_at: datetime
    report: list = []
