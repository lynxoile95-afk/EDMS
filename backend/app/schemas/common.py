"""Shared schema primitives."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str
    code: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20

    @property
    def pages(self) -> int:
        return (self.total + self.size - 1) // self.size


class Pagination(BaseModel):
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class DateDisplay(BaseModel):
    """Every timestamp is returned in UTC ISO form plus a Jalali display string."""

    utc: datetime | None = None
    jalali: str | None = None


class JalaliDate(BaseModel):
    iso: date | None = None
    jalali: str | None = None


class IdResponse(BaseModel):
    id: uuid.UUID
