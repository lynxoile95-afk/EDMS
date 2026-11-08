"""Project access circles and their people.

A circle is deliberately separate from a project role. Roles describe what a
person does in the document workflow; a circle describes who may collaborate
with whom. The two dimensions are therefore both visible and independently
editable on the classic users-and-roles screen.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.models import AccessCircle, AccessCircleMember, ProjectMember, User
from app.schemas.circles import (
    CircleCreate,
    CircleMemberCreate,
    CircleMemberOut,
    CircleMemberUpdate,
    CircleOut,
    CircleUpdate,
)
from app.services import acl, audit
from app.models.enums import ProjectRole

router = APIRouter(prefix="/projects/{project_id}/circles", tags=["access-circles"])
MANAGER_ROLES = [
    ProjectRole.DCC,
    ProjectRole.PROJECT_CONTROL,
    ProjectRole.PMO,
    ProjectRole.PROJECT_MANAGER,
    ProjectRole.SYSTEM_ADMIN,
]


def _can_manage(db, user: User, project_id: uuid.UUID) -> None:
    acl.require_project(db, user, project_id, MANAGER_ROLES)


def _circle_or_404(db, project_id: uuid.UUID, circle_id: uuid.UUID) -> AccessCircle:
    circle = db.get(AccessCircle, circle_id)
    if circle is None or circle.project_id != project_id:
        raise HTTPException(status_code=404, detail="حلقه دسترسی یافت نشد.")
    return circle


def _roles_for(db, project_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
    return sorted(
        {
            str(role)
            for role in db.execute(
                select(ProjectMember.role).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                )
            ).scalars()
        }
    )


def _member_out(db, member: AccessCircleMember, project_id: uuid.UUID) -> CircleMemberOut:
    person = member.user
    return CircleMemberOut(
        id=member.id,
        circle_id=member.circle_id,
        user_id=member.user_id,
        full_name=person.full_name,
        email=person.email,
        national_id=person.national_id,
        mobile=person.mobile,
        roles=_roles_for(db, project_id, person.id),
        can_access_external=member.can_access_external,
        is_active=person.is_active,
        created_at=member.created_at,
    )


def _circle_out(db, circle: AccessCircle) -> CircleOut:
    members = [_member_out(db, member, circle.project_id) for member in circle.members]
    return CircleOut(
        id=circle.id,
        project_id=circle.project_id,
        code=circle.code,
        name=circle.name,
        description=circle.description,
        is_active=circle.is_active,
        created_at=circle.created_at,
        member_count=len(members),
        external_member_count=sum(1 for member in members if member.can_access_external),
        members=members,
    )


@router.get("", response_model=list[CircleOut], summary="حلقه‌های دسترسی پروژه")
def list_circles(project: ProjectDep, db: DbSession, user: CurrentUser) -> list[CircleOut]:
    # A normal user sees only the circles to which they belong. Managers need the
    # complete roster to administer the project; an external member also needs to
    # see the boundaries they may route across.
    stmt = select(AccessCircle).where(AccessCircle.project_id == project.id).order_by(AccessCircle.code)
    if not user.is_superuser and not acl.has_role(db, user, project.id, *MANAGER_ROLES):
        own_ids = {
            item.id for item in acl.circle_memberships(db, user, project.id)
        }
        if not own_ids:
            return []
        stmt = stmt.where(AccessCircle.id.in_(own_ids))
    return [_circle_out(db, circle) for circle in db.execute(stmt).scalars()]


@router.post("", response_model=CircleOut, status_code=status.HTTP_201_CREATED, summary="ایجاد حلقه")
def create_circle(
    project: ProjectDep, payload: CircleCreate, db: DbSession, user: CurrentUser
) -> CircleOut:
    _can_manage(db, user, project.id)
    code = payload.code.strip().upper()
    if db.execute(
        select(AccessCircle).where(
            AccessCircle.project_id == project.id,
            AccessCircle.code == code,
        )
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="کد این حلقه قبلاً در پروژه ثبت شده است.")
    circle = AccessCircle(
        project_id=project.id,
        code=code,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        created_by_id=user.id,
    )
    db.add(circle)
    db.flush()
    audit.record(
        db,
        action="access_circle.create",
        entity_type="access_circle",
        entity_id=circle.id,
        actor=user,
        project_id=project.id,
        after={"code": circle.code, "name": circle.name},
    )
    db.commit()
    db.refresh(circle)
    return _circle_out(db, circle)


@router.patch("/{circle_id}", response_model=CircleOut, summary="ویرایش حلقه")
def update_circle(
    project: ProjectDep,
    circle_id: uuid.UUID,
    payload: CircleUpdate,
    db: DbSession,
    user: CurrentUser,
) -> CircleOut:
    _can_manage(db, user, project.id)
    circle = _circle_or_404(db, project.id, circle_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "name" and value is not None:
            value = value.strip()
        if field == "description" and value is not None:
            value = value.strip() or None
        setattr(circle, field, value)
    audit.record(
        db,
        action="access_circle.update",
        entity_type="access_circle",
        entity_id=circle.id,
        actor=user,
        project_id=project.id,
        after=updates,
    )
    db.commit()
    db.refresh(circle)
    return _circle_out(db, circle)


@router.delete("/{circle_id}", status_code=204, response_model=None, summary="حذف حلقه")
def delete_circle(
    project: ProjectDep, circle_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> None:
    _can_manage(db, user, project.id)
    circle = _circle_or_404(db, project.id, circle_id)
    audit.record(
        db,
        action="access_circle.delete",
        entity_type="access_circle",
        entity_id=circle.id,
        actor=user,
        project_id=project.id,
        before={"code": circle.code, "name": circle.name},
    )
    db.delete(circle)
    db.commit()


@router.post(
    "/{circle_id}/members",
    response_model=CircleOut,
    status_code=status.HTTP_201_CREATED,
    summary="افزودن فرد به حلقه",
)
def add_circle_member(
    project: ProjectDep,
    circle_id: uuid.UUID,
    payload: CircleMemberCreate,
    db: DbSession,
    user: CurrentUser,
) -> CircleOut:
    _can_manage(db, user, project.id)
    circle = _circle_or_404(db, project.id, circle_id)
    target = db.get(User, payload.user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="کاربر فعال یافت نشد.")
    if db.execute(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == target.id,
        )
    ).scalar_one_or_none() is None:
        raise HTTPException(status_code=422, detail="ابتدا این کاربر را با یک نقش به پروژه اضافه کنید.")

    existing = db.execute(
        select(AccessCircleMember).where(
            AccessCircleMember.circle_id == circle.id,
            AccessCircleMember.user_id == target.id,
        )
    ).scalar_one_or_none()
    if payload.can_access_external:
        other_external = db.execute(
            select(AccessCircleMember.id).where(
                AccessCircleMember.circle_id == circle.id,
                AccessCircleMember.can_access_external.is_(True),
                AccessCircleMember.user_id != target.id,
            )
        ).scalar_one_or_none()
        if other_external is not None:
            raise HTTPException(
                status_code=409,
                detail="هر حلقه فقط یک عضو با دسترسی خارج از حلقه می‌تواند داشته باشد.",
            )
    if existing is None:
        existing = AccessCircleMember(
            circle_id=circle.id,
            user_id=target.id,
            can_access_external=payload.can_access_external,
        )
        db.add(existing)
        db.flush()
    else:
        existing.can_access_external = payload.can_access_external
    audit.record(
        db,
        action="access_circle.member.add",
        entity_type="access_circle_member",
        entity_id=existing.id,
        actor=user,
        project_id=project.id,
        after={"circle": circle.code, "user": target.email, "can_access_external": existing.can_access_external},
    )
    db.commit()
    db.refresh(circle)
    return _circle_out(db, circle)


@router.patch(
    "/{circle_id}/members/{member_id}",
    response_model=CircleOut,
    summary="تغییر دسترسی خارجی عضو حلقه",
)
def update_circle_member(
    project: ProjectDep,
    circle_id: uuid.UUID,
    member_id: uuid.UUID,
    payload: CircleMemberUpdate,
    db: DbSession,
    user: CurrentUser,
) -> CircleOut:
    _can_manage(db, user, project.id)
    circle = _circle_or_404(db, project.id, circle_id)
    member = db.get(AccessCircleMember, member_id)
    if member is None or member.circle_id != circle.id:
        raise HTTPException(status_code=404, detail="عضویت حلقه یافت نشد.")
    if payload.can_access_external:
        other_external = db.execute(
            select(AccessCircleMember.id).where(
                AccessCircleMember.circle_id == circle.id,
                AccessCircleMember.can_access_external.is_(True),
                AccessCircleMember.id != member.id,
            )
        ).scalar_one_or_none()
        if other_external is not None:
            raise HTTPException(
                status_code=409,
                detail="هر حلقه فقط یک عضو با دسترسی خارج از حلقه می‌تواند داشته باشد.",
            )
    member.can_access_external = payload.can_access_external
    audit.record(
        db,
        action="access_circle.member.external_access",
        entity_type="access_circle_member",
        entity_id=member.id,
        actor=user,
        project_id=project.id,
        after={"can_access_external": member.can_access_external},
    )
    db.commit()
    db.refresh(circle)
    return _circle_out(db, circle)


@router.delete(
    "/{circle_id}/members/{member_id}",
    status_code=204,
    response_model=None,
    summary="حذف فرد از حلقه",
)
def remove_circle_member(
    project: ProjectDep,
    circle_id: uuid.UUID,
    member_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> None:
    _can_manage(db, user, project.id)
    circle = _circle_or_404(db, project.id, circle_id)
    member = db.get(AccessCircleMember, member_id)
    if member is None or member.circle_id != circle.id:
        raise HTTPException(status_code=404, detail="عضویت حلقه یافت نشد.")
    audit.record(
        db,
        action="access_circle.member.remove",
        entity_type="access_circle_member",
        entity_id=member.id,
        actor=user,
        project_id=project.id,
        before={"user_id": str(member.user_id), "circle": circle.code},
    )
    db.delete(member)
    db.commit()
