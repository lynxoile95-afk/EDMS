"""API contracts for authenticated, feature-gated Persian file QA."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FileQAStatusOut(BaseModel):
    enabled: bool
    backend: str
    advanced_supported: bool
    message: str | None = None
    embedding_model: str
    embedding_dimensions: int
    indexed_files: int = 0


class FileQADocumentOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    mime_type: str
    page_count: int
    indexed: bool
    updated_at: datetime | None = None


class FileQASourceOut(BaseModel):
    chunk_id: uuid.UUID
    file_id: uuid.UUID | None
    filename: str
    page_no: int | None
    source: str
    excerpt: str
    score: float
    page_url: str


class FileQAMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    sources: list[FileQASourceOut] = Field(default_factory=list)
    created_at: datetime | None = None


class FileQAAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    project_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None


class FileQAAskResponse(BaseModel):
    session_id: uuid.UUID
    answer: str
    sources: list[FileQASourceOut] = Field(default_factory=list)
    semantic_used: bool = False
    history: list[FileQAMessageOut] = Field(default_factory=list)


class FileQAClearRequest(BaseModel):
    session_id: uuid.UUID | None = None


class FileQARebuildRequest(BaseModel):
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    project_id: uuid.UUID | None = None
    force: bool = True


class RAGIndexJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_id: uuid.UUID
    project_id: uuid.UUID
    revision_id: uuid.UUID | None
    index_version: str
    model_name: str
    dimensions: int
    status: str
    cursor_chunk: int
    total_chunks: int
    attempt_count: int
    error_message: str | None
    force_reindex: bool
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None


class FileQAHistoryOut(BaseModel):
    session_id: uuid.UUID
    messages: list[FileQAMessageOut]
