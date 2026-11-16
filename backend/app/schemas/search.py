"""Schemas for content search/index administration."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchResultOut(BaseModel):
    chunk_id: uuid.UUID
    file_id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    page_no: int
    source_type: str
    snippet: str
    highlight: str
    score: float
    keyword_rank: int | None = None
    semantic_rank: int | None = None
    page_url: str


class SearchResponse(BaseModel):
    enabled: bool
    backend: str
    degraded: bool
    semantic_used: bool
    query: str
    items: list[SearchResultOut]
    total: int
    page: int
    limit: int


class SearchStatusOut(BaseModel):
    enabled: bool
    backend: str
    degraded: bool
    semantic_supported: bool
    message: str | None = None


class IndexJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_id: uuid.UUID
    project_id: uuid.UUID
    revision_id: uuid.UUID | None
    index_version: str
    status: str
    attempt_count: int
    cursor_page: int
    total_pages: int
    failed_pages: list[Any]
    error_message: str | None
    force_reindex: bool
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None


class ReindexRequest(BaseModel):
    file_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    force: bool = True
    limit: int = Field(default=100, ge=1, le=500)


class ReindexResponse(BaseModel):
    queued: int
    job_ids: list[uuid.UUID]
    next_cursor: int | None = None
    has_more: bool = False


class BackfillRequest(BaseModel):
    project_id: uuid.UUID | None = None
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)
    force: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    project_id: uuid.UUID | None = None
    limit: int = Field(default=8, ge=1, le=20)


class AskResponse(BaseModel):
    answer: str
    citations: list[SearchResultOut]
