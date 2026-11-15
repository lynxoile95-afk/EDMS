"""Request/response schemas for persisted AI credentials."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AICredentialUpsert(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(default="", max_length=255)
    extra: dict[str, Any] = Field(default_factory=dict)
    remote_consent: bool = False
    is_active: bool = False
    is_fallback: bool = False

    @field_validator("provider", "name", "base_url", "model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class AICredentialOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: uuid.UUID
    provider: str
    providerName: str
    apiType: str
    name: str
    baseUrl: str
    model: str
    extra: dict[str, Any]
    keyMasked: str | None
    keySet: bool
    status: str
    lastTestedAt: datetime | None
    latencyMs: int | None
    lastTestStatus: int | None
    lastError: str | None
    isActive: bool
    isFallback: bool
    remoteConsent: bool
    isLocal: bool
    visionCapableModels: list[str]


class AIConnectionTestRequest(AICredentialUpsert):
    credential_id: uuid.UUID | None = None


class AIActiveUpdate(BaseModel):
    credential_id: uuid.UUID | None = None
    fallback_credential_id: uuid.UUID | None = None


class AIModelsOut(BaseModel):
    provider: str
    credential_id: uuid.UUID
    models: list[str]
