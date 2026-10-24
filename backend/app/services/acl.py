"""Project- and discipline-scoped authorisation helpers.

Every endpoint resolves permissions through this module; no endpoint inspects
``ProjectMember`` directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, ProjectMember, User
from app.models.enums import ProjectRole


class AccessDenied(HTTPException):
    def __init__(self, detail: str = "دسترسی به این منبع مجاز نیست.") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class NotFound(HTTPException):
    def __init__(self, detail: str = "منبع یافت نشد.") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def memberships(db: Session, user: User) -> list[ProjectMember]:
    if user.is_superuser:
        return []
    return list(
        db.execute(select(ProjectMember).where(ProjectMember.user_id == user.id)).scalars()
    )


def accessible_project_ids(db: Session, user: User) -> list[uuid.UUID] | None:
    """``None`` means "all projects" (superuser)."""
    if user.is_superuser:
        return None
    return [m.project_id for m in memberships(db, user)]


def roles_in(db: Session, user: User, project_id: uuid.UUID) -> set[str]:
    if user.is_superuser:
        return {r.value for r in ProjectRole}
    rows = db.execute(
        select(ProjectMember.role).where(
            ProjectMember.user_id == user.id, ProjectMember.project_id == project_id
        )
    ).scalars()
    return {str(r) for r in rows}


def require_project(
    db: Session,
    user: User,
    project_id: uuid.UUID,
    roles: Iterable[ProjectRole] | None = None,
) -> Project:
    """Return the project or raise 403/404 (existence is not leaked)."""
    project = db.get(Project, project_id)
    if project is None:
        raise NotFound("پروژه یافت نشد.")
    if user.is_superuser:
        return project
    user_roles = roles_in(db, user, project_id)
    if not user_roles:
        # 404 (not 403) so non-members cannot probe project existence
        raise NotFound("پروژه یافت نشد.")
    if roles is not None and not (user_roles & {r.value for r in roles}):
        raise AccessDenied("نقش شما اجازه انجام این عملیات را ندارد.")
    return project


def has_role(db: Session, user: User, project_id: uuid.UUID, *roles: ProjectRole) -> bool:
    if user.is_superuser:
        return True
    return bool(roles_in(db, user, project_id) & {r.value for r in roles})


def assert_can_manage_documents(db: Session, user: User, project_id: uuid.UUID) -> None:
    if not has_role(db, user, project_id, ProjectRole.DCC, ProjectRole.PROJECT_MANAGER):
        raise AccessDenied("این عملیات تنها برای کنترل مدارک (DCC) یا مدیر پروژه مجاز است.")


def assert_can_review(db: Session, user: User, project_id: uuid.UUID) -> None:
    if not has_role(
        db,
        user,
        project_id,
        ProjectRole.REVIEWER,
        ProjectRole.DCC,
        ProjectRole.PROJECT_MANAGER,
    ):
        raise AccessDenied("این عملیات تنها برای کارشناس بازبین یا DCC مجاز است.")


def assert_admin(user: User) -> None:
    if not user.is_superuser:
        raise AccessDenied("این عملیات تنها برای مدیر سیستم مجاز است.")
