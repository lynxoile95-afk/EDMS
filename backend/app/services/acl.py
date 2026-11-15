"""Project- and discipline-scoped authorisation helpers.

Every endpoint resolves permissions through this module; no endpoint inspects
``ProjectMember`` directly. Access circles are an additional, project-scoped
boundary: a circle member can work with the other members of that circle, while
the explicit ``can_access_external`` flag opens that person's view and routing
outside the circle.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from app.models import AccessCircle, AccessCircleMember, Project, ProjectMember, Transmittal, User
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
    return list(db.execute(select(ProjectMember).where(ProjectMember.user_id == user.id)).scalars())


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
    if not has_role(
        db,
        user,
        project_id,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
    ):
        raise AccessDenied("این عملیات تنها برای کنترل مدارک، کنترل پروژه، PMO یا مدیر پروژه مجاز است.")


def assert_can_review(db: Session, user: User, project_id: uuid.UUID) -> None:
    if not has_role(
        db,
        user,
        project_id,
        ProjectRole.REVIEWER,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
    ):
        raise AccessDenied("این عملیات تنها برای کارشناس بازبین یا DCC مجاز است.")


def assert_admin(user: User) -> None:
    if not user.is_superuser:
        raise AccessDenied("این عملیات تنها برای مدیر سیستم مجاز است.")


# ---------------------------------------------------------------------------
# Access circles
# ---------------------------------------------------------------------------


def circle_memberships(
    db: Session, user: User, project_id: uuid.UUID, *, active_only: bool = True
) -> list[AccessCircleMember]:
    stmt = (
        select(AccessCircleMember)
        .join(AccessCircle, AccessCircle.id == AccessCircleMember.circle_id)
        .where(
            AccessCircle.project_id == project_id,
            AccessCircleMember.user_id == user.id,
        )
    )
    if active_only:
        stmt = stmt.where(AccessCircle.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def circle_member_ids(db: Session, circle_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        db.execute(
            select(AccessCircleMember.user_id).where(AccessCircleMember.circle_id == circle_id)
        ).scalars()
    )


def has_external_access(db: Session, user: User, project_id: uuid.UUID) -> bool:
    if user.is_superuser:
        return True
    return bool(
        db.execute(
            select(AccessCircleMember.id)
            .join(AccessCircle, AccessCircle.id == AccessCircleMember.circle_id)
            .where(
                AccessCircle.project_id == project_id,
                AccessCircle.is_active.is_(True),
                AccessCircleMember.user_id == user.id,
                AccessCircleMember.can_access_external.is_(True),
            )
            .limit(1)
        ).scalar_one_or_none()
    )


def is_circle_member(
    db: Session, user_id: uuid.UUID, project_id: uuid.UUID, circle_id: uuid.UUID
) -> bool:
    return bool(
        db.execute(
            select(AccessCircleMember.id)
            .join(AccessCircle, AccessCircle.id == AccessCircleMember.circle_id)
            .where(
                AccessCircle.id == circle_id,
                AccessCircle.project_id == project_id,
                AccessCircle.is_active.is_(True),
                AccessCircleMember.user_id == user_id,
            )
            .limit(1)
        ).scalar_one_or_none()
    )


def validate_circle_for_transmittal(
    db: Session,
    user: User,
    project_id: uuid.UUID,
    circle_id: uuid.UUID | None,
) -> None:
    """Validate the selected boundary before a new transmittal is created."""
    if circle_id is None or user.is_superuser:
        return
    circle = db.get(AccessCircle, circle_id)
    if circle is None or circle.project_id != project_id or not circle.is_active:
        raise AccessDenied("حلقه انتخاب‌شده به این پروژه تعلق ندارد یا فعال نیست.")
    if not is_circle_member(db, user.id, project_id, circle_id) and not has_external_access(
        db, user, project_id
    ):
        raise AccessDenied("شما عضو این حلقه نیستید و اجازه ساخت برگه در آن را ندارید.")


def can_access_transmittal(db: Session, user: User, transmittal: Transmittal) -> bool:
    """Whether a user may view a transmittal and its protected attachments."""
    if user.is_superuser or transmittal.access_circle_id is None:
        return True
    if has_external_access(db, user, transmittal.project_id):
        return True
    return is_circle_member(
        db, user.id, transmittal.project_id, transmittal.access_circle_id
    )


def require_transmittal(
    db: Session,
    user: User,
    transmittal: Transmittal,
    roles: Iterable[ProjectRole] | None = None,
) -> Project:
    project = require_project(db, user, transmittal.project_id, roles)
    if not can_access_transmittal(db, user, transmittal):
        # Do not reveal that a sheet exists outside the caller's circle.
        raise NotFound("برگه ارسال یافت نشد.")
    return project


def transmittal_visibility_clause(db: Session, user: User):
    """SQLAlchemy predicate for project transmittal listing.

    NULL ``access_circle_id`` is intentionally legacy-compatible: old sheets
    remain visible to project members. New sheets created from the circle-aware
    UI carry a circle and are filtered here at the database query boundary.
    """
    if user.is_superuser:
        return true()
    circle_ids = (
        select(AccessCircleMember.circle_id)
        .join(AccessCircle, AccessCircle.id == AccessCircleMember.circle_id)
        .where(
            AccessCircle.project_id == Transmittal.project_id,
            AccessCircle.is_active.is_(True),
            AccessCircleMember.user_id == user.id,
        )
    )
    external = (
        select(AccessCircleMember.id)
        .join(AccessCircle, AccessCircle.id == AccessCircleMember.circle_id)
        .where(
            AccessCircle.project_id == Transmittal.project_id,
            AccessCircle.is_active.is_(True),
            AccessCircleMember.user_id == user.id,
            AccessCircleMember.can_access_external.is_(True),
        )
        .exists()
    )
    return or_(Transmittal.access_circle_id.is_(None), Transmittal.access_circle_id.in_(circle_ids), external)


def can_route_to(
    db: Session,
    actor: User,
    target: User,
    transmittal: Transmittal,
) -> bool:
    """Whether a referral target is inside the actor's collaboration boundary."""
    if target.id == actor.id or actor.is_superuser:
        return True
    if not target.is_active:
        return False
    target_is_project_member = bool(
        db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == transmittal.project_id,
                ProjectMember.user_id == target.id,
            )
        ).scalar_one_or_none()
    )
    if not target_is_project_member:
        return False
    if transmittal.access_circle_id is None:
        return True
    if has_external_access(db, actor, transmittal.project_id) or has_external_access(
        db, target, transmittal.project_id
    ):
        return True
    return is_circle_member(
        db, actor.id, transmittal.project_id, transmittal.access_circle_id
    ) and is_circle_member(
        db, target.id, transmittal.project_id, transmittal.access_circle_id
    )


def assert_can_route_to(
    db: Session,
    actor: User,
    target: User,
    transmittal: Transmittal,
) -> None:
    if not can_route_to(db, actor, target, transmittal):
        raise AccessDenied(
            "کاربر مقصد خارج از حلقه دسترسی شماست؛ فقط اعضای همین حلقه یا کاربر مجاز به دسترسی خارجی قابل ارجاع هستند."
        )


def visible_user_ids(
    db: Session, user: User, project_id: uuid.UUID, circle_id: uuid.UUID | None = None
) -> set[uuid.UUID] | None:
    """IDs suitable for a referral/recipient picker, or None for all users."""
    if user.is_superuser or has_external_access(db, user, project_id):
        return None
    if circle_id is not None:
        return circle_member_ids(db, circle_id) if is_circle_member(db, user.id, project_id, circle_id) else set()
    own_circle_ids = {
        member.circle_id for member in circle_memberships(db, user, project_id)
    }
    if not own_circle_ids:
        has_any_circle = bool(
            db.execute(
                select(AccessCircle.id).where(
                    AccessCircle.project_id == project_id,
                    AccessCircle.is_active.is_(True),
                ).limit(1)
            ).scalar_one_or_none()
        )
        # Projects created before access circles existed keep the original
        # project-wide recipient picker and routing behaviour.
        return None if not has_any_circle else {user.id}
    return set(
        db.execute(
            select(AccessCircleMember.user_id).where(AccessCircleMember.circle_id.in_(own_circle_ids))
        ).scalars()
    )


def assert_recipients_allowed(
    db: Session,
    actor: User,
    project_id: uuid.UUID,
    recipient_ids: Iterable[uuid.UUID],
    circle_id: uuid.UUID | None,
) -> None:
    """Reject recipients outside the selected circle before a draft is committed."""
    target_ids = set(recipient_ids)
    if not target_ids:
        return
    targets = list(
        db.execute(
            select(User).where(User.id.in_(target_ids), User.is_active.is_(True))
        ).scalars()
    )
    if {target.id for target in targets} != target_ids:
        raise AccessDenied("همه گیرندگان باید کاربر فعال باشند.")
    if not target_ids.issubset(
        {
            member_id
            for member_id in db.execute(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id.in_(target_ids),
                )
            ).scalars()
        }
    ):
        raise AccessDenied("همه گیرندگان باید عضو همین پروژه باشند.")
    if circle_id is None:
        return
    # Use the same interaction rule as a referral, without requiring a fully
    # materialised transmittal object.
    for target in targets:
        if not is_circle_member(db, actor.id, project_id, circle_id) and not has_external_access(
            db, actor, project_id
        ):
            raise AccessDenied("شما عضو حلقه انتخاب‌شده نیستید.")
        if target.id == actor.id or has_external_access(db, actor, project_id) or has_external_access(
            db, target, project_id
        ):
            continue
        if target.id not in circle_member_ids(db, circle_id):
            raise AccessDenied("یکی از گیرندگان خارج از حلقه انتخاب‌شده است.")
