"""System administration: users, audit trail, operational status."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.security import hash_password
from app.models import AuditLog, Project, User
from app.schemas.auth import UserCreate, UserOut
from app.services import acl, audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserOut], summary="فهرست کاربران")
def list_users(
    db: DbSession, user: CurrentUser, q: str | None = Query(None), limit: int = Query(100, le=500)
) -> list[UserOut]:
    acl.assert_admin(user)
    stmt = select(User).order_by(User.full_name)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(User.full_name.ilike(like) | User.email.ilike(like))
    return [UserOut.model_validate(row) for row in db.execute(stmt.limit(limit)).scalars()]


@router.post("/users", response_model=UserOut, status_code=201, summary="ایجاد کاربر")
def create_user(payload: UserCreate, db: DbSession, user: CurrentUser) -> UserOut:
    acl.assert_admin(user)
    if db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="این ایمیل قبلاً ثبت شده است.")
    new_user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        is_superuser=payload.is_superuser,
        locale=payload.locale,
        auth_provider="local",
    )
    db.add(new_user)
    db.flush()
    audit.record(
        db, action="user.create", entity_type="user", entity_id=new_user.id, actor=user,
        after={"email": new_user.email, "superuser": new_user.is_superuser},
    )
    db.commit()
    return UserOut.model_validate(new_user)


@router.patch("/users/{user_id}/active", response_model=UserOut, summary="فعال/غیرفعال کردن کاربر")
def set_active(user_id: uuid.UUID, active: bool, db: DbSession, user: CurrentUser) -> UserOut:
    acl.assert_admin(user)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")
    if target.id == user.id and not active:
        raise HTTPException(status_code=422, detail="نمی‌توانید حساب خود را غیرفعال کنید.")
    target.is_active = active
    audit.record(
        db, action="user.set_active", entity_type="user", entity_id=target.id, actor=user,
        after={"is_active": active},
    )
    db.commit()
    return UserOut.model_validate(target)


@router.get("/audit", summary="لاگ ممیزی")
def audit_log(
    db: DbSession,
    user: CurrentUser,
    project_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    acl.assert_admin(user)
    stmt = select(AuditLog)
    if project_id:
        stmt = stmt.where(AuditLog.project_id == project_id)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)).scalars()
    return {
        "total": total,
        "items": [
            {
                "id": str(row.id),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "actor": row.actor_email,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "before": row.before,
                "after": row.after,
                "meta": row.meta,
                "ip": row.ip_address,
            }
            for row in rows
        ],
    }


@router.get("/ai-status", summary="وضعیت لایه هوش مصنوعی (VLM)")
def ai_status(user: CurrentUser) -> dict:
    """Is an AI key configured, is the endpoint local, and does it answer?

    The key itself is never returned — only whether one is set. A remote endpoint is
    reported as *refused* rather than *reachable*: confidential drawings may only be
    processed on-premise, so the client would refuse to use it either way.
    """
    acl.assert_admin(user)
    from app.core.config import settings
    from app.extraction.vlm import VLMExtractor

    vlm = VLMExtractor()
    key_set = bool(settings.vlm_api_key and settings.vlm_api_key != "EMPTY")
    allowed = vlm.is_local_endpoint()
    reachable: bool | None = None
    probe_error: str | None = None
    if settings.vlm_enabled and allowed:
        try:
            import httpx

            response = httpx.get(
                f"{settings.vlm_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.vlm_api_key}"},
                timeout=3.0,
            )
            reachable = response.status_code < 500
        except Exception as exc:  # noqa: BLE001 - the reason is the payload
            reachable = False
            probe_error = str(exc)[:200]
    return {
        "enabled": settings.vlm_enabled,
        "key_set": key_set,
        "model": settings.vlm_model,
        "base_url": settings.vlm_base_url,
        "endpoint_allowed": allowed,
        "reachable": reachable,
        "probe_error": probe_error,
        "where_to_set_fa": (
            "کلید در فایل .env ریشه پروژه در متغیرهای VLM_API_KEY / VLM_BASE_URL / VLM_MODEL "
            "قرار می‌گیرد و پس از تغییر باید سرویس api و worker دوباره راه‌اندازی شوند. "
            "برای فعال شدن واقعی، VLM_ENABLED=true و در تنظیمات پروژه vlm_fallback=true لازم است."
        ),
    }


@router.get("/status", summary="وضعیت عملیاتی")
def status(db: DbSession, user: CurrentUser) -> dict:
    acl.assert_admin(user)
    from app.db.session import ping

    from app.core.config import settings

    return {
        "database": ping(),
        "storage_backend": settings.storage_backend,
        "ocr_engine": settings.ocr_engine,
        "vlm_enabled": settings.vlm_enabled,
        "environment": settings.environment,
        "warnings": settings.validate_runtime(),
        "projects": db.execute(select(func.count(Project.id))).scalar_one(),
        "users": db.execute(select(func.count(User.id))).scalar_one(),
    }
