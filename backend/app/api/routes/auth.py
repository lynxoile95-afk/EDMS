"""Authentication endpoints (local provider now; LDAP/OIDC pluggable)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, HTTPException, Response, Request, status
from sqlalchemy import select

from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, CurrentUser, DbSession
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair, summary="ورود با ایمیل و رمز عبور")
def login(payload: LoginRequest, request: Request, response: Response, db: DbSession) -> TokenPair:
    user = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()
    if user is None or not user.is_active or not user.hashed_password:
        # constant-ish response: never reveal whether the address exists
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ایمیل یا رمز عبور نادرست است.")
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ایمیل یا رمز عبور نادرست است.")

    user.last_login_at = datetime.now(timezone.utc)
    audit.record(
        db,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        project_id=None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    pair = TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
        expires_in=settings.access_token_expire_minutes * 60,
    )
    _set_auth_cookies(response, pair)
    return pair


def _set_auth_cookies(response: Response, pair: TokenPair) -> None:
    """Mirror the tokens into cookies as a second, header-independent transport."""
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(
        ACCESS_COOKIE,
        pair.access_token,
        max_age=pair.expires_in,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        pair.refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


@router.post(
    "/logout",
    status_code=204,
    summary="خروج و پاک‌سازی نشست",
    description="کوکی‌های نشست را پاک می‌کند؛ توکن‌های ارسالی در بدنه هم بی‌اثر می‌شوند.",
)
def logout(response: Response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(name, path="/")


@router.post("/refresh", response_model=TokenPair, summary="تمدید نشست")
def refresh(payload: RefreshRequest, request: Request, response: Response, db: DbSession) -> TokenPair:
    raw_token = payload.refresh_token or request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="توکن تمدید ارسال نشده است.")
    try:
        claims = decode_token(raw_token, "refresh")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="توکن تمدید نامعتبر است.") from exc
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, TypeError, ValueError) as exc:
        # A signed token whose subject is not a UUID must be a 401, never a 500:
        # SQLAlchemy's UUID type would otherwise fail on ``value.hex``.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="توکن تمدید نامعتبر است.") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="کاربر یافت نشد.")
    pair = TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
        expires_in=settings.access_token_expire_minutes * 60,
    )
    _set_auth_cookies(response, pair)
    return pair


@router.get(
    "/transport-check",
    summary="بررسی مسیر انتقال توکن (عیب‌یابی)",
    description=(
        "بدون نیاز به احراز هویت، نشان می‌دهد توکن از کدام مسیر رسیده است "
        "(هدر Authorization، هدر X-Access-Token، کوکی یا query) و آیا معتبر است. "
        "برای تشخیص پروکسی‌هایی که هدرها را حذف می‌کنند."
    ),
)
def transport_check(request: Request) -> dict:
    from app.api.deps import extract_access_token

    token, sources = extract_access_token(request, None)
    valid = False
    subject: str | None = None
    if token:
        try:
            claims = decode_token(token, "access")
            valid = True
            subject = str(claims.get("sub"))
        except jwt.PyJWTError:
            valid = False
    header_names = sorted({name.lower() for name in request.headers.keys()})
    return {
        "token_sources": sources,
        "token_present": bool(token),
        "token_valid": valid,
        "subject": subject,
        "cookies_received": sorted(request.cookies.keys()),
        "authorization_header_present": "authorization" in header_names,
        "x_access_token_header_present": "x-access-token" in header_names,
        "headers": header_names,
    }


@router.get("/me", response_model=UserOut, summary="پروفایل کاربر جاری")
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/change-password", status_code=204, summary="تغییر رمز عبور")
def change_password(payload: PasswordChange, user: CurrentUser, db: DbSession) -> None:
    if not user.hashed_password or not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="رمز عبور فعلی نادرست است.")
    user.hashed_password = hash_password(payload.new_password)
    audit.record(db, action="auth.change_password", entity_type="user", entity_id=user.id, actor=user)
    db.commit()
