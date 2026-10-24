from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=4, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    # Optional: browser sessions may carry the token in the refresh cookie instead.
    refresh_token: str | None = None


class MembershipOut(ORMModel):
    project_id: uuid.UUID
    role: str
    discipline_id: uuid.UUID | None = None


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    locale: str
    auth_provider: str
    last_login_at: datetime | None = None
    memberships: list[MembershipOut] = []


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    is_superuser: bool = False
    locale: str = "fa"


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class MemberCreate(BaseModel):
    user_id: uuid.UUID
    role: str
    discipline_id: uuid.UUID | None = None


class MemberOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    role: str
    discipline_id: uuid.UUID | None = None
    user: UserOut | None = None
