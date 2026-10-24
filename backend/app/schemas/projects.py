from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ProjectCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=2, max_length=255)
    client_name: str | None = None
    contractor_name: str | None = None
    description: str | None = None
    settings: dict | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    client_name: str | None = None
    contractor_name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class ProjectSettingsUpdate(BaseModel):
    settings: dict


class MemberOut(BaseModel):
    """One project membership: who, in which role, on which discipline."""

    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str
    role: str
    discipline_id: uuid.UUID | None = None
    discipline_code: str | None = None
    discipline_name: str | None = None
    is_active: bool = True


class ProjectOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    client_name: str | None = None
    contractor_name: str | None = None
    description: str | None = None
    is_active: bool
    created_at: datetime
    settings: dict = {}


class ProjectSummary(ProjectOut):
    document_count: int = 0
    member_role: str | None = None


class LookupCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)


class LookupOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool


class TitleBlockTemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    page_aspect_ratios: list[list[float]] = []
    regions: dict = {}
    anchor_keywords: list[str] = []
    field_regex: dict = {}
    priority: int = 100


class TitleBlockTemplateUpdate(BaseModel):
    name: str | None = None
    page_aspect_ratios: list[list[float]] | None = None
    regions: dict | None = None
    anchor_keywords: list[str] | None = None
    field_regex: dict | None = None
    priority: int | None = None
    is_active: bool | None = None


class TitleBlockTemplateOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    page_aspect_ratios: list = []
    regions: dict = {}
    anchor_keywords: list = []
    field_regex: dict = {}
    priority: int
    is_active: bool
    version: int
    created_at: datetime
