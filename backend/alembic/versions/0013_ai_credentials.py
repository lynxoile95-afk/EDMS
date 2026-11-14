"""Persist encrypted AI provider credentials and active selection.

Revision ID: 0013_ai_credentials
Revises: 0012_reply_sheet_workflow
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from urllib.parse import urlparse

import sqlalchemy as sa
from alembic import context, op

revision: str = "0013_ai_credentials"
down_revision: str | None = "0012_reply_sheet_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _migrate_legacy_vlm_settings() -> None:
    """Move the old VLM_* environment configuration into the first row.

    This is intentionally online-only: offline SQL generation must never embed an
    API key in a generated migration script. The service-level migration remains as
    a fallback for installations that created the tables outside Alembic.
    """
    if context.is_offline_mode():
        return

    from app.ai.crypto import encrypt_secret
    from app.ai.registry import get_provider
    from app.core.config import settings

    bind = op.get_bind()
    provider_id = (settings.vlm_provider or "openai").strip().lower()
    catalog = get_provider(provider_id) or get_provider("openai") or {}
    provider_id = catalog.get("id", "openai")
    base_url = (settings.vlm_base_url or catalog.get("defaultBaseUrl", "")).strip().rstrip("/")
    parsed = urlparse(base_url)
    if provider_id == "gemini" and parsed.hostname and parsed.hostname.endswith("googleapis.com") and not parsed.path.strip("/"):
        base_url = f"{base_url}/v1beta"
    configured_model = (settings.vlm_model or "").strip()
    model = configured_model or str(catalog.get("defaultModel", ""))
    if provider_id == "gemini" and (not configured_model or "/" in configured_model or not configured_model.startswith("gemini")):
        model = str(catalog.get("defaultModel", "gemini-2.0-flash"))

    key = (settings.vlm_api_key or "").strip()
    credential_id = uuid.uuid4() if key and key != "EMPTY" else None
    if credential_id is not None:
        bind.execute(
            sa.table(
                "ai_credentials",
                sa.column("id", sa.Uuid()),
                sa.column("provider_id"),
                sa.column("name"),
                sa.column("api_type"),
                sa.column("base_url"),
                sa.column("model"),
                sa.column("api_key_encrypted"),
                sa.column("extra", sa.JSON()),
                sa.column("remote_consent"),
                sa.column("enabled"),
                sa.column("status"),
                sa.column("is_active"),
                sa.column("is_fallback"),
            ).insert().values(
                id=credential_id,
                provider_id=provider_id,
                name=f"{catalog.get('name', provider_id)} – main",
                api_type=catalog.get("apiType", "openai-compatible"),
                base_url=base_url,
                model=model,
                api_key_encrypted=encrypt_secret(key),
                extra={},
                remote_consent=bool(settings.vlm_allow_remote),
                enabled=bool(settings.vlm_enabled),
                status="untested",
                is_active=True,
                is_fallback=False,
            )
        )

    bind.execute(
        sa.table(
            "ai_settings",
            sa.column("id", sa.Uuid()),
            sa.column("active_credential_id", sa.Uuid()),
            sa.column("legacy_migrated"),
        ).insert().values(
            id=uuid.uuid4(),
            active_credential_id=credential_id,
            legacy_migrated=True,
        )
    )


def upgrade() -> None:
    op.create_table(
        "ai_credentials",
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("api_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("remote_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="untested"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_test_status", sa.Integer(), nullable=True),
        sa.Column("last_test_message", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "name", name="uq_ai_credential_provider_name"),
    )
    op.create_index("ix_ai_credential_active", "ai_credentials", ["is_active"], unique=False)
    op.create_index("ix_ai_credential_provider", "ai_credentials", ["provider_id"], unique=False)

    op.create_table(
        "ai_settings",
        sa.Column("singleton_key", sa.String(length=32), nullable=False, server_default="default"),
        sa.Column("active_credential_id", sa.Uuid(), nullable=True),
        sa.Column("fallback_credential_id", sa.Uuid(), nullable=True),
        sa.Column("legacy_migrated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["active_credential_id"], ["ai_credentials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fallback_credential_id"], ["ai_credentials.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key"),
    )

    _migrate_legacy_vlm_settings()


def downgrade() -> None:
    op.drop_table("ai_settings")
    op.drop_index("ix_ai_credential_provider", table_name="ai_credentials")
    op.drop_index("ix_ai_credential_active", table_name="ai_credentials")
    op.drop_table("ai_credentials")
