from __future__ import annotations

import uuid
from datetime import datetime

import re

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel


_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def _digits(value: str) -> str:
    return value.translate(_DIGIT_TRANSLATION).replace(" ", "").replace("-", "")


def _validate_national_id(value: str) -> str:
    normalized = _digits(value)
    if not re.fullmatch(r"\d{10}", normalized):
        raise ValueError("کد ملی باید دقیقاً ۱۰ رقم باشد.")
    if len(set(normalized)) == 1:
        raise ValueError("کد ملی واردشده معتبر نیست.")
    check = int(normalized[9])
    weighted = sum(int(normalized[index]) * (10 - index) for index in range(9)) % 11
    if weighted >= 2:
        weighted = 11 - weighted
    if check != weighted:
        raise ValueError("رقم کنترل کد ملی صحیح نیست.")
    return normalized


def _validate_mobile(value: str) -> str:
    normalized = _digits(value)
    if normalized.startswith("0098"):
        normalized = "0" + normalized[4:]
    elif normalized.startswith("+98"):
        normalized = "0" + normalized[3:]
    if not re.fullmatch(r"09\d{9}", normalized):
        raise ValueError("شماره موبایل باید به شکل 09xxxxxxxxx باشد.")
    return normalized


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
    national_id: str | None = None
    mobile: str | None = None
    is_active: bool
    is_superuser: bool
    locale: str
    auth_provider: str
    last_login_at: datetime | None = None
    memberships: list[MembershipOut] = []


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    national_id: str
    mobile: str
    password: str = Field(min_length=8, max_length=128)
    is_superuser: bool = False
    locale: str = "fa"

    _normalize_national_id = field_validator("national_id")(_validate_national_id)
    _normalize_mobile = field_validator("mobile")(_validate_mobile)


class UserUpdate(BaseModel):
    # All identity values are editable from the administration screen. Omitting
    # password keeps the current password; an empty password is never accepted.
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    national_id: str | None = None
    mobile: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_superuser: bool | None = None
    locale: str | None = Field(default=None, min_length=2, max_length=8)

    @field_validator("national_id")
    @classmethod
    def validate_optional_national_id(cls, value: str | None) -> str | None:
        return _validate_national_id(value) if value is not None and value.strip() else None

    @field_validator("mobile")
    @classmethod
    def validate_optional_mobile(cls, value: str | None) -> str | None:
        return _validate_mobile(value) if value is not None and value.strip() else None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class MemberCreate(BaseModel):
    user_id: uuid.UUID
    role: str = Field(min_length=1, max_length=255)
    discipline_id: uuid.UUID | None = None
    discipline_name: str | None = Field(default=None, max_length=255)


class MemberUpdate(BaseModel):
    role: str = Field(min_length=1, max_length=255)
    discipline_id: uuid.UUID | None = None
    discipline_name: str | None = Field(default=None, max_length=255)


class MemberOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    role: str
    discipline_id: uuid.UUID | None = None
    user: UserOut | None = None
