from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class CircleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class CircleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class CircleMemberCreate(BaseModel):
    user_id: uuid.UUID
    can_access_external: bool = False


class CircleMembersBulkCreate(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    can_access_external: bool = False
    # When an account has no membership in this project yet, these optional
    # fields preserve the legacy "add to project and circle" flow. Existing
    # project memberships are never overwritten by this request.
    role: str | None = Field(default=None, max_length=255)
    discipline_id: uuid.UUID | None = None
    discipline_name: str | None = Field(default=None, max_length=255)


class CircleMemberUpdate(BaseModel):
    can_access_external: bool


class CircleMemberOut(ORMModel):
    id: uuid.UUID
    circle_id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str
    national_id: str | None = None
    mobile: str | None = None
    roles: list[str] = []
    can_access_external: bool
    is_active: bool
    created_at: datetime


class CircleOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    is_active: bool
    created_at: datetime
    member_count: int = 0
    external_member_count: int = 0
    members: list[CircleMemberOut] = []
