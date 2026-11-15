"""Persisted, encrypted AI provider credentials and active selection."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONBType, TimestampMixin, UUIDPrimaryKeyMixin


class AICredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One named provider credential.

    The API key is always stored in ``api_key_encrypted``.  It is deliberately
    not exposed through ``as_dict`` or any response schema; callers must use the
    credential service which decrypts it only immediately before a server-side
    provider request.
    """

    __tablename__ = "ai_credentials"
    __table_args__ = (
        UniqueConstraint("provider_id", "name", name="uq_ai_credential_provider_name"),
        Index("ix_ai_credential_active", "is_active"),
        Index("ix_ai_credential_provider", "provider_id"),
    )

    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    api_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    model: Mapped[str] = mapped_column(String(255), default="", server_default="")
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONBType, default=dict)
    remote_consent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    status: Mapped[str] = mapped_column(String(16), default="untested", server_default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_test_status: Mapped[int | None] = mapped_column(Integer)
    last_test_message: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AISettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Singleton pointer for active/fallback credentials and one-time migration."""

    __tablename__ = "ai_settings"
    singleton_key: Mapped[str] = mapped_column(String(32), unique=True, default="default", server_default="default")
    active_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_credentials.id", ondelete="SET NULL")
    )
    fallback_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_credentials.id", ondelete="SET NULL")
    )
    legacy_migrated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
