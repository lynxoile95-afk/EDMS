"""System administration: users, audit trail, operational status."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession
from app.core.security import hash_password
from app.models import AuditLog, Project, ProjectMember, User
from pydantic import BaseModel, Field

from app.api.routes.projects import _resolve_member_discipline, _resolve_member_role
from app.schemas.auth import UserCreate, UserOut, UserUpdate
from app.services import acl, audit

router = APIRouter(prefix="/admin", tags=["admin"])


def _identity_conflict(db, *, email: str, national_id: str | None, mobile: str | None, exclude_id=None) -> User | None:
    conditions = [User.email == email.lower()]
    if national_id:
        conditions.append(User.national_id == national_id)
    if mobile:
        conditions.append(User.mobile == mobile)
    stmt = select(User).where(or_(*conditions))
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return db.execute(stmt).scalars().first()


def _save_project_membership(
    db,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    role_value: str,
    discipline_id: uuid.UUID | None,
    discipline_name: str | None,
    actor: User,
    existing: ProjectMember | None = None,
) -> ProjectMember:
    """Create or update one project membership for the admin user form.

    Account administration is deliberately the only place that may save the
    account and its first project membership in one transaction. The same role
    and discipline resolvers used by the project-members endpoint are reused so
    aliases keep routing permissions while unknown typed labels remain intact.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="پروژه یافت نشد.")
    if not role_value.strip():
        raise HTTPException(status_code=422, detail="نقش پروژه نمی‌تواند خالی باشد.")

    role = _resolve_member_role(role_value)
    discipline = _resolve_member_discipline(db, project.id, discipline_id, discipline_name)
    resolved_discipline_id = discipline.id if discipline is not None else None

    duplicate_stmt = select(ProjectMember.id).where(
        ProjectMember.project_id == project.id,
        ProjectMember.user_id == user_id,
        ProjectMember.role == role,
    )
    if existing is not None:
        duplicate_stmt = duplicate_stmt.where(ProjectMember.id != existing.id)
    if resolved_discipline_id is None:
        duplicate_stmt = duplicate_stmt.where(ProjectMember.discipline_id.is_(None))
    else:
        duplicate_stmt = duplicate_stmt.where(ProjectMember.discipline_id == resolved_discipline_id)
    if db.execute(duplicate_stmt).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="این کاربر با همین نقش و دیسیپلین قبلاً عضو پروژه است.")

    if existing is None:
        member = ProjectMember(
            project_id=project.id,
            user_id=user_id,
            role=role,
            discipline_id=resolved_discipline_id,
        )
        db.add(member)
        db.flush()
        action = "project.member.add"
        before = None
    else:
        member = existing
        action = "project.member.update"
        before = {
            "role": str(member.role),
            "discipline_id": str(member.discipline_id) if member.discipline_id else None,
        }
        member.role = role
        member.discipline_id = resolved_discipline_id
        db.flush()

    audit.record(
        db,
        action=action,
        entity_type="project_member",
        entity_id=member.id,
        actor=actor,
        project_id=project.id,
        before=before,
        after={
            "role": role,
            "discipline_id": str(resolved_discipline_id) if resolved_discipline_id else None,
            "discipline_name": discipline.name if discipline else None,
        },
    )
    return member


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
    conflict = _identity_conflict(
        db,
        email=str(payload.email),
        national_id=payload.national_id,
        mobile=payload.mobile,
    )
    if conflict is not None:
        if conflict.email == str(payload.email).lower():
            detail = "این ایمیل قبلاً ثبت شده است."
        elif conflict.national_id == payload.national_id:
            detail = "این کد ملی قبلاً برای کاربر دیگری ثبت شده است."
        else:
            detail = "این شماره موبایل قبلاً برای کاربر دیگری ثبت شده است."
        raise HTTPException(status_code=409, detail=detail)
    new_user = User(
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        national_id=payload.national_id,
        mobile=payload.mobile,
        hashed_password=hash_password(payload.password),
        is_superuser=payload.is_superuser,
        locale=payload.locale,
        auth_provider="local",
    )
    db.add(new_user)
    db.flush()
    if payload.project_id is not None:
        if payload.role is None:
            raise HTTPException(status_code=422, detail="برای عضویت در پروژه، نقش پروژه الزامی است.")
        _save_project_membership(
            db,
            project_id=payload.project_id,
            user_id=new_user.id,
            role_value=payload.role,
            discipline_id=payload.discipline_id,
            discipline_name=payload.discipline_name,
            actor=user,
        )
    elif payload.role is not None or payload.discipline_id is not None or payload.discipline_name:
        raise HTTPException(status_code=422, detail="برای ثبت نقش یا دیسیپلین، پروژه را انتخاب کنید.")
    audit.record(
        db, action="user.create", entity_type="user", entity_id=new_user.id, actor=user,
        after={
            "email": new_user.email,
            "superuser": new_user.is_superuser,
            "project_id": str(payload.project_id) if payload.project_id else None,
            "role": payload.role,
        },
    )
    db.commit()
    db.refresh(new_user)
    return UserOut.model_validate(new_user)


@router.patch("/users/{user_id}", response_model=UserOut, summary="ویرایش مشخصات کاربر")
def update_user(user_id: uuid.UUID, payload: UserUpdate, db: DbSession, user: CurrentUser) -> UserOut:
    acl.assert_admin(user)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")

    raw_updates = payload.model_dump(exclude_unset=True)
    membership_keys = {"role", "discipline_id", "discipline_name"}
    has_membership_update = bool(membership_keys.intersection(raw_updates))
    project_id = raw_updates.pop("project_id", None)
    role_value = raw_updates.pop("role", None)
    discipline_id_provided = "discipline_id" in raw_updates
    discipline_name_provided = "discipline_name" in raw_updates
    discipline_id_value = raw_updates.pop("discipline_id", None)
    discipline_name_value = raw_updates.pop("discipline_name", None)

    if has_membership_update:
        if project_id is None:
            raise HTTPException(status_code=422, detail="برای ویرایش نقش یا دیسیپلین، پروژه را انتخاب کنید.")
        existing = db.execute(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.user_id == target.id)
            .order_by(ProjectMember.created_at, ProjectMember.id)
            .limit(1)
        ).scalars().first()
        if role_value is None and existing is None:
            raise HTTPException(status_code=422, detail="برای عضویت در پروژه، نقش پروژه الزامی است.")
        effective_role = role_value or str(existing.role)
        if discipline_id_provided:
            effective_discipline_id = discipline_id_value
            effective_discipline_name = (
                discipline_name_value
                if discipline_name_provided
                else (existing.discipline.name if existing and discipline_id_value else None)
            )
        elif discipline_name_provided:
            effective_discipline_id = None
            effective_discipline_name = discipline_name_value
        else:
            effective_discipline_id = existing.discipline_id if existing else None
            effective_discipline_name = existing.discipline.name if existing and existing.discipline else None
        _save_project_membership(
            db,
            project_id=project_id,
            user_id=target.id,
            role_value=effective_role,
            discipline_id=effective_discipline_id,
            discipline_name=effective_discipline_name,
            actor=user,
            existing=existing,
        )

    candidate_email = str(raw_updates.get("email", target.email)).lower()
    candidate_national_id = raw_updates.get("national_id", target.national_id)
    candidate_mobile = raw_updates.get("mobile", target.mobile)
    conflict = _identity_conflict(
        db,
        email=candidate_email,
        national_id=candidate_national_id,
        mobile=candidate_mobile,
        exclude_id=target.id,
    )
    if conflict is not None:
        if conflict.email == candidate_email:
            raise HTTPException(status_code=409, detail="این ایمیل قبلاً ثبت شده است.")
        if conflict.national_id == candidate_national_id:
            raise HTTPException(status_code=409, detail="این کد ملی قبلاً ثبت شده است.")
        raise HTTPException(status_code=409, detail="این شماره موبایل قبلاً ثبت شده است.")

    before = {
        "email": target.email,
        "full_name": target.full_name,
        "national_id": target.national_id,
        "mobile": target.mobile,
        "is_superuser": target.is_superuser,
    }
    if "email" in raw_updates:
        raw_updates["email"] = candidate_email
    if "password" in raw_updates:
        target.hashed_password = hash_password(raw_updates.pop("password")) if raw_updates["password"] else target.hashed_password
        raw_updates.pop("password", None)
    for field, value in raw_updates.items():
        setattr(target, field, value)
    audit.record(
        db,
        action="user.update",
        entity_type="user",
        entity_id=target.id,
        actor=user,
        before=before,
        after={key: value for key, value in raw_updates.items() if key != "password"},
    )
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)


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
    if payload.api_key is not None:
        # An empty string means "forget the key" — the only other way to remove a key
        # entered for a test was restarting the service.
        key = payload.api_key.strip()
        settings.vlm_api_key = key or "EMPTY"
        if not key:
            settings.vlm_enabled = False
    if payload.enabled is not None:
        settings.vlm_enabled = payload.enabled
    elif payload.api_key and payload.api_key.strip():
        settings.vlm_enabled = True

    after = {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "enabled": settings.vlm_enabled,
        "allow_remote": allow_remote,
        # never the key itself, only the fact that it changed
        "key_updated": bool(payload.api_key and payload.api_key.strip()),
        "key_cleared": payload.api_key is not None and not payload.api_key.strip(),
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
