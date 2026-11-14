"""Persisted AI provider credentials and server-side connection tests."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session

from app.ai import service
from app.ai.registry import get_provider, public_provider_catalog
from app.api.deps import CurrentUser, DbSession
from app.models import AICredential
from app.schemas.ai import AIActiveUpdate, AICredentialOut, AICredentialUpsert, AIConnectionTestRequest
from app.services import acl, audit

router = APIRouter(prefix="/ai", tags=["ai-credentials"])


def _admin(user) -> None:
    acl.assert_admin(user)


def _summary_response(row: AICredential) -> dict[str, Any]:
    return service.credential_summary(row)


def _runtime_from_payload(payload: AIConnectionTestRequest) -> service.RuntimeCredential:
    provider = get_provider(payload.provider.strip().lower())
    if provider is None:
        raise ValueError("سرویس‌دهنده انتخاب‌شده در فهرست پشتیبانی‌شده نیست.")
    _, base_url, model, extra = service.validate_provider_input(
        provider_id=provider["id"],
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key or "",
        extra=payload.extra,
    )
    return service.RuntimeCredential(
        credential_id=payload.credential_id,
        provider_id=provider["id"],
        api_type=provider["apiType"],
        base_url=base_url,
        model=model,
        api_key=(payload.api_key or "").strip(),
        extra=extra,
        remote_consent=payload.remote_consent,
        enabled=True,
    )


@router.get("/providers", summary="فهرست سرویس‌دهنده‌های هوش مصنوعی")
def providers(user: CurrentUser) -> list[dict[str, Any]]:
    _admin(user)
    return public_provider_catalog()


@router.get("/credentials", response_model=list[AICredentialOut], summary="فهرست Credentialهای هوش مصنوعی")
def list_ai_credentials(db: DbSession, user: CurrentUser) -> list[dict[str, Any]]:
    _admin(user)
    try:
        rows = service.list_credentials(db)
        db.commit()
        return rows
    except Exception:
        db.rollback()
        raise


@router.post("/credentials", response_model=AICredentialOut, summary="ذخیره Credential هوش مصنوعی")
def save_ai_credential(payload: AICredentialUpsert, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    try:
        # Complete the one-time env migration before accepting a new row so a
        # later GET cannot mistake this user-created Credential for legacy state.
        service.ensure_legacy_migration(db)
        row = service.save_credential(
            db,
            provider_id=payload.provider,
            name=payload.name,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
            extra=payload.extra,
            remote_consent=payload.remote_consent,
            created_by_id=user.id,
            is_active=payload.is_active,
            is_fallback=payload.is_fallback,
        )
        audit.record(
            db,
            action="ai.credential.save",
            entity_type="ai_credential",
            entity_id=row.id,
            actor=user,
            after={"provider": row.provider_id, "name": row.name, "model": row.model},
        )
        db.commit()
        db.refresh(row)
        return _summary_response(row)
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/credentials/{credential_id}/key", response_model=AICredentialOut, summary="پاک کردن کلید Credential")
def clear_ai_key(credential_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    try:
        row = service.clear_credential_key(db, credential_id)
        audit.record(
            db,
            action="ai.credential.key_clear",
            entity_type="ai_credential",
            entity_id=credential_id,
            actor=user,
            after={"key_cleared": True},
        )
        db.commit()
        db.refresh(row)
        return _summary_response(row)
    except KeyError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/credentials/{credential_id}", status_code=204, response_model=None, summary="حذف Credential")
def remove_ai_credential(credential_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    _admin(user)
    try:
        row = db.get(AICredential, credential_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Credential پیدا نشد.")
        provider_name = row.provider_id
        credential_name = row.name
        service.delete_credential(db, credential_id)
        audit.record(
            db,
            action="ai.credential.delete",
            entity_type="ai_credential",
            entity_id=credential_id,
            actor=user,
            before={"provider": provider_name, "name": credential_name},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise


@router.post("/credentials/{credential_id}/test", summary="آزمایش Credential ذخیره‌شده")
def test_saved_credential(credential_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    row = db.get(AICredential, credential_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Credential پیدا نشد.")
    try:
        runtime = service._row_runtime(row)  # noqa: SLF001 - route is the persistence boundary
        result = service.test_credential(runtime)
        service.record_test(row, result)
        db.commit()
        return service.result_payload(result)
    except Exception:
        db.rollback()
        raise


@router.post("/test", summary="آزمایش فرم ذخیره‌نشده")
def test_unsaved_credential(payload: AIConnectionTestRequest, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    try:
        runtime = _runtime_from_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return service.result_payload(service.test_credential(runtime))


@router.get("/models", summary="دریافت مدل‌های زنده سرویس‌دهنده")
def list_live_models(
    db: DbSession,
    user: CurrentUser,
    provider: str = Query(..., min_length=1),
    credential_id: uuid.UUID = Query(..., alias="credentialId"),
) -> dict[str, Any]:
    _admin(user)
    row = db.get(AICredential, credential_id)
    if row is None or row.provider_id != provider.strip().lower():
        raise HTTPException(status_code=404, detail="Credential سرویس‌دهنده پیدا نشد.")
    catalog = get_provider(row.provider_id) or {}
    if not catalog.get("supportsModelList"):
        return {"provider": row.provider_id, "credentialId": str(row.id), "models": []}
    try:
        models = service._row_runtime(row).adapter().list_models()  # noqa: SLF001
        return {"provider": row.provider_id, "credentialId": str(row.id), "models": models}
    except Exception as exc:  # noqa: BLE001 - adapter turns HTTP errors into safe text
        raise HTTPException(status_code=502, detail=f"دریافت فهرست مدل‌ها ناموفق بود: {str(exc)[:300]}") from exc


@router.put("/active", summary="انتخاب Credential فعال و جایگزین")
def update_active(
    payload: AIActiveUpdate,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    _admin(user)
    try:
        prefs = service.set_active_credentials(
            db,
            active_id=payload.credential_id,
            fallback_id=payload.fallback_credential_id,
        )
        audit.record(
            db,
            action="ai.credential.activate",
            entity_type="ai_settings",
            entity_id=prefs.id,
            actor=user,
            after={
                "active_credential_id": str(prefs.active_credential_id) if prefs.active_credential_id else None,
                "fallback_credential_id": str(prefs.fallback_credential_id) if prefs.fallback_credential_id else None,
            },
        )
        db.commit()
        active = db.get(AICredential, prefs.active_credential_id) if prefs.active_credential_id else None
        fallback = db.get(AICredential, prefs.fallback_credential_id) if prefs.fallback_credential_id else None
        return {
            "activeCredentialId": str(active.id) if active else None,
            "fallbackCredentialId": str(fallback.id) if fallback else None,
            "activeProvider": active.provider_id if active else None,
            "fallbackProvider": fallback.provider_id if fallback else None,
        }
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
