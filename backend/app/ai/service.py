"""Credential lifecycle, legacy migration and active-provider resolution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.adapters import AIAdapter, ConnectionResult, ProviderCredential, build_adapter
from app.ai.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.ai.registry import get_provider
from app.core.config import settings
from app.models import AICredential, AISettings


@dataclass(slots=True)
class RuntimeCredential:
    credential_id: uuid.UUID | None
    provider_id: str
    api_type: str
    base_url: str
    model: str
    api_key: str
    extra: dict[str, Any]
    remote_consent: bool
    enabled: bool

    def adapter(self, client_factory=None) -> AIAdapter:
        kwargs = {} if client_factory is None else {"client_factory": client_factory}
        return build_adapter(
            ProviderCredential(
                provider_id=self.provider_id,
                api_type=self.api_type,
                base_url=self.base_url,
                model=self.model,
                api_key=self.api_key,
                extra=self.extra,
            ),
            **kwargs,
        )


def _settings_row(db: Session, *, create: bool = True) -> AISettings | None:
    row = db.execute(select(AISettings).where(AISettings.singleton_key == "default")).scalar_one_or_none()
    if row is None and create:
        row = AISettings(singleton_key="default")
        db.add(row)
        db.flush()
    return row


def _env_runtime() -> RuntimeCredential:
    provider_id = (settings.vlm_provider or "gemini").strip().lower()
    catalog = get_provider(provider_id) or get_provider("gemini") or {}
    base_url = (settings.vlm_base_url or catalog.get("defaultBaseUrl", "")).strip().rstrip("/")
    if provider_id == "gemini":
        parsed_base = urlparse(base_url)
        if parsed_base.hostname and parsed_base.hostname.endswith("googleapis.com") and not parsed_base.path.strip("/"):
            base_url = f"{base_url}/v1beta"
    configured_model = (settings.vlm_model or "").strip()
    model = configured_model or str(catalog.get("defaultModel", ""))
    # The historical defaults are OpenAI/local-model defaults. When an existing
    # installation switches VLM_PROVIDER to Gemini without also changing the old
    # model variable, migrate the documented Gemini model instead of persisting a
    # Qwen-style identifier that can never work against the Gemini endpoint.
    if provider_id == "gemini" and (not configured_model or "/" in configured_model or not configured_model.startswith("gemini")):
        model = str(catalog.get("defaultModel", "gemini-2.0-flash"))
    api_key = settings.vlm_api_key if settings.vlm_api_key != "EMPTY" else ""
    return RuntimeCredential(
        credential_id=None,
        provider_id=catalog.get("id", provider_id),
        api_type=catalog.get("apiType", "openai-compatible"),
        base_url=base_url,
        model=model,
        api_key=api_key,
        extra={},
        remote_consent=bool(settings.vlm_allow_remote),
        enabled=bool(settings.vlm_enabled),
    )


def ensure_legacy_migration(db: Session) -> AISettings:
    """Create the first credential from the existing VLM_* environment once.

    The marker lives independently from credentials so deleting the migrated row
    does not unexpectedly recreate it on the next page load.
    """
    prefs = _settings_row(db)
    assert prefs is not None
    if prefs.legacy_migrated:
        return prefs

    existing = db.execute(select(AICredential).order_by(AICredential.created_at, AICredential.id)).scalars().first()
    if existing is None:
        runtime = _env_runtime()
        # Do not manufacture an empty Credential on a fresh installation. A
        # configured legacy key (or an explicitly enabled local VLM) is the signal
        # that there is something to migrate; otherwise the first credential is
        # created deliberately through the new API.
        if runtime.api_key or runtime.enabled:
            catalog = get_provider(runtime.provider_id) or {}
            key = runtime.api_key.strip()
            existing = AICredential(
                provider_id=runtime.provider_id,
                name=f"{catalog.get('name', runtime.provider_id)} – main",
                api_type=runtime.api_type,
                base_url=runtime.base_url or catalog.get("defaultBaseUrl", ""),
                model=runtime.model,
                api_key_encrypted=encrypt_secret(key) if key else None,
                remote_consent=runtime.remote_consent,
                enabled=runtime.enabled,
                status="untested",
                is_active=True,
            )
            db.add(existing)
            db.flush()
    if existing is not None:
        prefs.active_credential_id = existing.id
        existing.is_active = True
    prefs.legacy_migrated = True
    db.flush()
    return prefs


def _row_runtime(row: AICredential) -> RuntimeCredential:
    return RuntimeCredential(
        credential_id=row.id,
        provider_id=row.provider_id,
        api_type=row.api_type,
        base_url=row.base_url,
        model=row.model,
        api_key=decrypt_secret(row.api_key_encrypted),
        extra=dict(row.extra or {}),
        remote_consent=bool(row.remote_consent),
        enabled=bool(row.enabled),
    )


def active_runtime(db: Session | None = None) -> RuntimeCredential:
    """Resolve the active DB credential, falling back to the old env settings."""
    if db is not None:
        prefs = _settings_row(db, create=False)
        row = None
        if prefs and prefs.active_credential_id:
            row = db.get(AICredential, prefs.active_credential_id)
        if row is None:
            row = db.execute(select(AICredential).where(AICredential.is_active.is_(True))).scalars().first()
        if row is not None:
            return _row_runtime(row)
        return _env_runtime()

    # The extraction worker does not carry a request session. Use its normal
    # SessionLocal so it sees persisted credentials, but keep the environment path
    # as a safe fallback for test/dev installs that have not run migration 0013.
    try:
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            return active_runtime(session)
    except Exception:  # noqa: BLE001 - legacy env configuration must keep uploads alive
        return _env_runtime()


def fallback_runtime(db: Session | None = None) -> RuntimeCredential | None:
    """Resolve the optional fallback without ever exposing its decrypted key."""
    if db is not None:
        prefs = _settings_row(db, create=False)
        row = db.get(AICredential, prefs.fallback_credential_id) if prefs and prefs.fallback_credential_id else None
        if row is None:
            row = db.execute(
                select(AICredential).where(AICredential.is_fallback.is_(True)).order_by(AICredential.created_at)
            ).scalars().first()
        return _row_runtime(row) if row is not None else None

    try:
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            return fallback_runtime(session)
    except Exception:  # noqa: BLE001 - env-only installations have no AI tables yet
        return None


def credential_summary(row: AICredential) -> dict[str, Any]:
    catalog = get_provider(row.provider_id) or {}
    try:
        key = decrypt_secret(row.api_key_encrypted)
    except Exception:  # noqa: BLE001 - show a failed state, never the ciphertext
        key = ""
    return {
        "id": str(row.id),
        "provider": row.provider_id,
        "providerName": catalog.get("name", row.provider_id),
        "apiType": row.api_type,
        "name": row.name,
        "baseUrl": row.base_url,
        "model": row.model,
        "extra": row.extra or {},
        "keyMasked": mask_secret(key),
        "keySet": bool(key),
        "status": row.status,
        "lastTestedAt": row.last_tested_at.isoformat() if row.last_tested_at else None,
        "latencyMs": row.last_test_latency_ms,
        "lastTestStatus": row.last_test_status,
        "lastError": row.last_test_message if row.status == "failed" else None,
        "isActive": bool(row.is_active),
        "isFallback": bool(row.is_fallback),
        "remoteConsent": bool(row.remote_consent),
        "isLocal": bool(catalog.get("isLocal", False)),
        "visionCapableModels": catalog.get("visionCapableModels", []),
    }


def list_credentials(db: Session) -> list[dict[str, Any]]:
    ensure_legacy_migration(db)
    rows = db.execute(select(AICredential).order_by(AICredential.created_at, AICredential.name)).scalars()
    return [credential_summary(row) for row in rows]


def _validate_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL باید یک آدرس معتبر با http:// یا https:// باشد.")
    if any(character.isspace() for character in url):
        raise ValueError("base URL نباید فاصله داشته باشد.")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL نباید query یا fragment داشته باشد؛ کلید را هرگز در URL قرار ندهید.")
    return url


def validate_provider_input(
    *,
    provider_id: str,
    base_url: str,
    model: str,
    api_key: str,
    extra: dict[str, Any],
    require_key: bool = True,
) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    catalog = get_provider(provider_id)
    if catalog is None:
        raise ValueError("سرویس‌دهنده انتخاب‌شده در فهرست پشتیبانی‌شده نیست.")
    normalized_url = _validate_url(base_url)
    if require_key and catalog.get("keyRequired") and not api_key.strip():
        raise ValueError("برای این سرویس‌دهنده وارد کردن API Key الزامی است.")
    if not model.strip():
        raise ValueError("نام مدل نمی‌تواند خالی باشد.")
    normalized_extra = {str(key): value for key, value in (extra or {}).items()}
    for field in catalog.get("extraFields", []):
        if field.get("required") and not str(normalized_extra.get(field["id"], "")).strip():
            raise ValueError(f"فیلد «{field.get('label', field['id'])}» الزامی است.")
    return catalog, normalized_url, model.strip(), normalized_extra


def save_credential(
    db: Session,
    *,
    provider_id: str,
    name: str,
    base_url: str,
    model: str,
    api_key: str | None,
    extra: dict[str, Any],
    remote_consent: bool,
    created_by_id: uuid.UUID,
    is_active: bool = False,
    is_fallback: bool = False,
) -> AICredential:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("نام Credential نمی‌تواند خالی باشد.")
    normalized_provider_id = provider_id.strip().lower()
    if get_provider(normalized_provider_id) is None:
        raise ValueError("سرویس‌دهنده انتخاب‌شده در فهرست پشتیبانی‌شده نیست.")
    row = db.execute(
        select(AICredential).where(
            AICredential.provider_id == normalized_provider_id,
            AICredential.name == clean_name,
        )
    ).scalar_one_or_none()
    catalog, clean_url, clean_model, clean_extra = validate_provider_input(
        provider_id=normalized_provider_id,
        base_url=base_url,
        model=model,
        api_key=(api_key or ""),
        extra=extra,
        # An omitted/blank key on an existing credential means "keep the current
        # encrypted key". Explicit removal has its own DELETE endpoint.
        require_key=False,
    )
    provider_id = catalog["id"]
    old_key = decrypt_secret(row.api_key_encrypted) if row and row.api_key_encrypted else ""
    effective_key = old_key if api_key is None else api_key.strip()
    if catalog.get("keyRequired") and not effective_key:
        raise ValueError("برای این سرویس‌دهنده API Key تنظیم نشده است.")

    if row is None:
        row = AICredential(
            provider_id=provider_id,
            name=clean_name,
            api_type=catalog["apiType"],
            base_url=clean_url,
            model=clean_model,
            api_key_encrypted=encrypt_secret(effective_key) if effective_key else None,
            extra=clean_extra,
            remote_consent=remote_consent,
            created_by_id=created_by_id,
            status="untested",
        )
        db.add(row)
    else:
        row.api_type = catalog["apiType"]
        row.base_url = clean_url
        row.model = clean_model
        row.extra = clean_extra
        row.remote_consent = remote_consent
        if api_key is not None:
            row.api_key_encrypted = encrypt_secret(effective_key) if effective_key else None
        row.status = "untested"
        row.last_tested_at = None
        row.last_test_latency_ms = None
        row.last_test_status = None
        row.last_test_message = None
    db.flush()
    if is_active or is_fallback:
        prefs = _settings_row(db)
        assert prefs is not None
        set_active_credentials(
            db,
            active_id=row.id if is_active else prefs.active_credential_id,
            fallback_id=row.id if is_fallback else prefs.fallback_credential_id,
        )
    return row


def set_active_credentials(db: Session, *, active_id: uuid.UUID | None, fallback_id: uuid.UUID | None) -> AISettings:
    prefs = _settings_row(db)
    assert prefs is not None
    active = db.get(AICredential, active_id) if active_id else None
    fallback = db.get(AICredential, fallback_id) if fallback_id else None
    if active_id and active is None:
        raise ValueError("Credential فعال پیدا نشد.")
    if fallback_id and fallback is None:
        raise ValueError("Credential جایگزین پیدا نشد.")
    if active and fallback and active.id == fallback.id:
        raise ValueError("Credential فعال و جایگزین باید متفاوت باشند.")
    if active and not bool((get_provider(active.provider_id) or {}).get("isLocal", False)) and not active.remote_consent:
        raise PermissionError("برای فعال کردن سرویس ابری، تأیید ارسال به سرور عمومی الزامی است.")
    if fallback and not bool((get_provider(fallback.provider_id) or {}).get("isLocal", False)) and not fallback.remote_consent:
        raise PermissionError("برای فعال کردن سرویس جایگزین ابری، تأیید ارسال به سرور عمومی الزامی است.")
    for row in db.execute(select(AICredential)).scalars():
        row.is_active = bool(active and row.id == active.id)
        row.is_fallback = bool(fallback and row.id == fallback.id)
    if active is not None:
        active.enabled = True
    if fallback is not None:
        fallback.enabled = True
    prefs.active_credential_id = active.id if active else None
    prefs.fallback_credential_id = fallback.id if fallback else None
    db.flush()
    return prefs


def clear_credential_key(db: Session, credential_id: uuid.UUID) -> AICredential:
    row = db.get(AICredential, credential_id)
    if row is None:
        raise KeyError("Credential پیدا نشد.")
    row.api_key_encrypted = None
    row.status = "untested"
    row.last_tested_at = None
    row.last_test_latency_ms = None
    row.last_test_status = None
    row.last_test_message = None
    db.flush()
    return row


def delete_credential(db: Session, credential_id: uuid.UUID) -> None:
    row = db.get(AICredential, credential_id)
    if row is None:
        raise KeyError("Credential پیدا نشد.")
    prefs = _settings_row(db, create=False)
    if prefs:
        if prefs.active_credential_id == row.id:
            prefs.active_credential_id = None
        if prefs.fallback_credential_id == row.id:
            prefs.fallback_credential_id = None
    db.delete(row)
    db.flush()


def test_credential(runtime: RuntimeCredential, *, client_factory=None) -> ConnectionResult:
    return runtime.adapter(client_factory).test_connection()


def record_test(row: AICredential, result: ConnectionResult) -> None:
    row.status = "connected" if result.ok else "failed"
    row.last_tested_at = datetime.now(timezone.utc)
    row.last_test_latency_ms = result.latency_ms
    row.last_test_status = result.status
    row.last_test_message = result.message if not result.ok else None


def result_payload(result: ConnectionResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status": result.status,
        "message": result.message,
        "latencyMs": result.latency_ms,
        "model": result.model,
        "models": result.models,
        "details": result.raw_error,
    }


def active_provider_is_remote(runtime: RuntimeCredential) -> bool:
    return not bool((get_provider(runtime.provider_id) or {}).get("isLocal", False))
