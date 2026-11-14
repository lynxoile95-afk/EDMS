"""FastAPI dependencies: DB session, current user, project scoping."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models import Project, User
from app.services import acl

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

#: Cookie names used by the SPA as a transport-safe alternative to ``Authorization``.
ACCESS_COOKIE = "edms_access_token"
REFRESH_COOKIE = "edms_refresh_token"


def extract_access_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> tuple[str | None, list[str]]:
    """Read the access token from any supported carrier.

    Browsers behind reverse proxies sometimes lose custom headers (the header can
    be consumed by the proxy for its own auth, or stripped by a corporate middle
    box). Accepting a cookie and a dedicated header as well makes the session
    survive any single hop that drops one of them. The order is: explicit
    ``Authorization`` first (API clients), then ``X-Access-Token``, then cookie,
    then query string (for redirect/download flows).
    """
    sources: list[str] = []
    token = credentials.credentials if credentials and credentials.credentials else None
    if not token:
        raw = request.headers.get("authorization", "")
        if raw.lower().startswith("bearer "):
            token = raw[7:].strip() or None
    if token:
        sources.append("authorization")

    if not token:
        header_token = request.headers.get("x-access-token")
        if header_token:
            token = header_token.strip()
            sources.append("x-access-token")

    if not token:
        cookie_token = request.cookies.get(ACCESS_COOKIE)
        if cookie_token:
            token = cookie_token
            sources.append("cookie")

    if not token:
        query_token = request.query_params.get("access_token")
        if query_token:
            token = query_token
            sources.append("query")

    return token, sources

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="احراز هویت لازم است.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token, sources = extract_access_token(request, credentials)
    if not token:
        logger.warning("401 on %s %s: no credentials were received", request.method, request.url.path)
        raise unauthorized
    try:
        payload = decode_token(token, "access")
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="نشست منقضی شده است؛ دوباره وارد شوید."
        ) from exc
    except jwt.PyJWTError as exc:
        logger.warning(
            "401 on %s %s: token from %s rejected (%s)",
            request.method,
            request.url.path,
            ",".join(sources) or "unknown",
            type(exc).__name__,
        )
        raise unauthorized from exc

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise unauthorized from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_project(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> Project:
    return acl.require_project(db, user, project_id)


ProjectDep = Annotated[Project, Depends(get_project)]


def get_page(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200)) -> tuple[int, int]:
    return page, size


PageDep = Annotated[tuple[int, int], Depends(get_page)]


def require_dcc(db: DbSession, project: ProjectDep, user: CurrentUser) -> None:
    acl.assert_can_manage_documents(db, user, project.id)


def require_admin(user: CurrentUser) -> None:
    acl.assert_admin(user)


AdminUser = Annotated[User, Depends(lambda user=Depends(get_current_user): (acl.assert_admin(user), user)[1])]
