"""System administration: users, audit trail, operational status."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.security import hash_password
from app.models import AuditLog, Project, User
from pydantic import BaseModel, Field

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


# --------------------------------------------------------------- AI configuration
#
# The key is write-only: no endpoint ever returns it, not even masked. The values set
# here live in the process memory, which is what "موقتاً تست کنیم" needs — nothing is
# written to disk and nothing can be committed by accident. For a permanent
# configuration put the same values in .env (VLM_*) and restart the services.

GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"


class AIConfigUpdate(BaseModel):
    """Everything except the key is optional; the key stays untouched when omitted."""

    provider: str | None = Field(default=None, description="openai | gemini")
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=400)
    enabled: bool | None = None
    allow_remote: bool | None = None
    #: sending drawings to a public endpoint is confidential-data egress: the caller
    #: has to say so in the same request, not rely on a default.
    confirm_remote_acknowledged: bool = False


def _ai_config_out() -> dict:
    from app.core.config import settings
    from app.extraction.vlm import VLMExtractor

    vlm = VLMExtractor()
    key = settings.vlm_api_key or ""
    return {
        "provider": settings.vlm_provider,
        "base_url": settings.vlm_base_url,
        "model": settings.vlm_model,
        "enabled": settings.vlm_enabled,
        "allow_remote": settings.vlm_allow_remote,
        "key_set": bool(key and key != "EMPTY"),
        "endpoint_local": vlm.is_local_endpoint(),
        "endpoint_trusted": vlm.endpoint_trusted(),
        "applies_until": "restart",
        "note_fa": (
            "کلید هرگز برگردانده نمی‌شود و در هیچ صفحه‌ای نمایش داده نمی‌شود. این مقادیر "
            "فقط تا راه‌اندازی مجدد سرویس معتبرند؛ برای دائمی شدن همان‌ها را در .env "
            "(VLM_PROVIDER / VLM_BASE_URL / VLM_API_KEY / VLM_MODEL) بگذارید."
        ),
    }


@router.get("/ai-config", summary="تنظیمات لایه هوش مصنوعی (بدون افشای کلید)")
def get_ai_config(user: CurrentUser) -> dict:
    acl.assert_admin(user)
    return _ai_config_out()


@router.put("/ai-config", summary="ورود موقت کلید و آدرس هوش مصنوعی")
def put_ai_config(payload: AIConfigUpdate, db: DbSession, user: CurrentUser) -> dict:
    """Set the VLM provider/key for this process.

    A public endpoint is refused unless ``allow_remote`` **and**
    ``confirm_remote_acknowledged`` arrive together, so no one can turn confidential
    egress on by accident or by a typo in a config file.
    """
    acl.assert_admin(user)
    from app.core.config import settings

    provider = (payload.provider or settings.vlm_provider or "openai").strip().lower()
    if provider not in {"openai", "gemini"}:
        raise HTTPException(status_code=422, detail="provider باید openai یا gemini باشد.")

    base_url = (payload.base_url or settings.vlm_base_url or "").strip().rstrip("/")
    model = (payload.model or settings.vlm_model or "").strip()
    if provider == "gemini":
        base_url = base_url or GEMINI_DEFAULT_BASE_URL
        # Be exact with whatever is given (self-hosted Gemini-compatible servers have
        # their own paths); only add the API version to a bare Google host, which is
        # the one shape people paste from the docs.
        host = base_url.split("//")[-1].split("/")[0]
        path = base_url[len(base_url.split("//")[0]) + 2 + len(host) :].strip("/")
        if "googleapis.com" in host and not path:
            base_url = f"{base_url.rstrip('/')}/v1beta"
        if not model or "/" in model:
            model = GEMINI_DEFAULT_MODEL
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="آدرس باید با http:// یا https:// شروع شود.")

    allow_remote = settings.vlm_allow_remote if payload.allow_remote is None else payload.allow_remote
    is_public = not base_url.split("//")[-1].split("/")[0].startswith(
        ("localhost", "127.0.0.1", "10.", "192.168.", "172.", "vlm", "ollama")
    )
    # Ask for the acknowledgement only when remote use is being turned **on**; once the
    # admin has accepted the risk, later edits (key, model, local URL) must not force
    # them through the same checkbox again.
    turning_on = allow_remote and not settings.vlm_allow_remote
    if turning_on and not payload.confirm_remote_acknowledged:
        raise HTTPException(
            status_code=400,
            detail=(
                "ارسال مدارک محرمانه به یک سرور عمومی باید صریحاً تأیید شود. "
                "گزینهٔ «تأیید ارسال به سرور عمومی» را هم انتخاب کنید."
            ),
        )
    # Setting a *new* public URL without the acknowledgement is refused. Turning the
    # switch back off while a public URL is merely still typed in must be allowed —
    # otherwise an admin could not lock the system down again.
    if is_public and not allow_remote and payload.base_url is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "آدرس واردشده عمومی است و طبق سیاست امنیتی پذیرفته نمی‌شود. "
                "یا آدرس داخلی بدهید، یا «تأیید ارسال به سرور عمومی» را تیک بزنید."
            ),
        )

    before = {
        "provider": settings.vlm_provider,
        "base_url": settings.vlm_base_url,
        "model": settings.vlm_model,
        "enabled": settings.vlm_enabled,
        "allow_remote": settings.vlm_allow_remote,
        "key_set": bool(settings.vlm_api_key and settings.vlm_api_key != "EMPTY"),
    }

    settings.vlm_provider = provider
    settings.vlm_base_url = base_url
    settings.vlm_model = model
    settings.vlm_allow_remote = allow_remote
    if payload.api_key is not None and payload.api_key.strip():
        settings.vlm_api_key = payload.api_key.strip()
    if payload.enabled is not None:
        settings.vlm_enabled = payload.enabled
    elif payload.api_key:
        settings.vlm_enabled = True

    after = {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "enabled": settings.vlm_enabled,
        "allow_remote": allow_remote,
        # never the key itself, only the fact that it changed
        "key_updated": bool(payload.api_key and payload.api_key.strip()),
    }
    audit.record(
        db, action="admin.ai_config.update", entity_type="settings", entity_id=None,
        actor=user, before=before, after=after,
    )
    db.commit()
    return {**_ai_config_out(), "changed": after}


@router.post("/ai-config/test", summary="آزمایش اتصال به سرویس هوش مصنوعی")
def test_ai_config(user: CurrentUser) -> dict:
    """Probe the configured endpoint and say, in Persian, why it failed."""
    acl.assert_admin(user)
    from app.extraction.vlm import VLMExtractor

    return VLMExtractor().probe()


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
    allowed = vlm.endpoint_trusted()
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
        "provider": settings.vlm_provider,
        "key_set": key_set,
        "model": settings.vlm_model,
        "base_url": settings.vlm_base_url,
        "endpoint_local": vlm.is_local_endpoint(),
        "remote_allowed": settings.vlm_allow_remote,
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
