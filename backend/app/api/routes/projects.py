"""Projects, disciplines, units, members, settings and title-block templates."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.models import (
    Discipline,
    DocumentRevision,
    MDLItem,
    Project,
    ProjectMember,
    TitleBlockTemplate,
    Unit,
    User,
)
from app.models.enums import ProjectRole
from app.schemas.auth import MemberCreate, MemberOut
from app.schemas.projects import (
    LookupCreate,
    LookupOut,
    ProjectCreate,
    ProjectOut,
    ProjectSettingsUpdate,
    ProjectSummary,
    ProjectUpdate,
    TitleBlockTemplateCreate,
    TitleBlockTemplateOut,
    TitleBlockTemplateUpdate,
)
from app.services import acl, audit
from app.services.project_settings import merge_settings

router = APIRouter(prefix="/projects", tags=["projects"])


def _role_of(db, user: User, project_id: uuid.UUID) -> str | None:
    roles = acl.roles_in(db, user, project_id)
    if not roles:
        return None
    ordered = ["dcc", "project_manager", "reviewer", "submitter", "system_admin"]
    for role in ordered:
        if role in roles:
            return role
    return sorted(roles)[0]


@router.get("", response_model=list[ProjectSummary], summary="پروژه‌های قابل دسترس")
def list_projects(db: DbSession, user: CurrentUser) -> list[ProjectSummary]:
    ids = acl.accessible_project_ids(db, user)
    stmt = select(Project).where(Project.is_active.is_(True)).order_by(Project.code)
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(Project.id.in_(ids))
    projects = list(db.execute(stmt).scalars())
    counts = dict(
        db.execute(
            select(MDLItem.project_id, func.count(MDLItem.id)).group_by(MDLItem.project_id)
        ).all()
    )
    return [
        ProjectSummary(
            **ProjectOut.model_validate(project).model_dump(),
            document_count=counts.get(project.id, 0),
            member_role=_role_of(db, user, project.id),
        )
        for project in projects
    ]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, summary="ایجاد پروژه (مدیر سیستم)")
def create_project(payload: ProjectCreate, db: DbSession, user: CurrentUser) -> ProjectOut:
    acl.assert_admin(user)
    if db.execute(select(Project).where(Project.code == payload.code)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="کد پروژه تکراری است.")
    project = Project(
        code=payload.code.upper(),
        name=payload.name,
        client_name=payload.client_name,
        contractor_name=payload.contractor_name,
        description=payload.description,
        settings=merge_settings(payload.settings),
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.SYSTEM_ADMIN))
    audit.record(
        db, action="project.create", entity_type="project", entity_id=project.id, actor=user,
        project_id=project.id, after={"code": project.code, "name": project.name},
    )
    db.commit()
    return ProjectOut.model_validate(project)


@router.get("/{project_id}", response_model=ProjectOut, summary="جزئیات پروژه")
def get_project_endpoint(project: ProjectDep) -> ProjectOut:
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectOut, summary="ویرایش پروژه")
def update_project(project: ProjectDep, payload: ProjectUpdate, db: DbSession, user: CurrentUser) -> ProjectOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    before = {"name": project.name, "client_name": project.client_name}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    audit.record(
        db, action="project.update", entity_type="project", entity_id=project.id, actor=user,
        project_id=project.id, before=before, after=payload.model_dump(exclude_unset=True),
    )
    db.commit()
    return ProjectOut.model_validate(project)


@router.get("/{project_id}/settings", summary="تنظیمات مؤثر پروژه")
def get_settings(project: ProjectDep) -> dict:
    return merge_settings(project.settings)


@router.put("/{project_id}/settings", response_model=ProjectOut, summary="به‌روزرسانی تنظیمات")
def update_settings(
    project: ProjectDep, payload: ProjectSettingsUpdate, db: DbSession, user: CurrentUser
) -> ProjectOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    before = dict(project.settings or {})
    project.settings = merge_settings(payload.settings)
    audit.record(
        db, action="project.settings.update", entity_type="project", entity_id=project.id,
        actor=user, project_id=project.id, before=before, after=project.settings,
    )
    db.commit()
    return ProjectOut.model_validate(project)


# ---------------------------------------------------------------------------
# Disciplines / Units
# ---------------------------------------------------------------------------
@router.get("/{project_id}/disciplines", response_model=list[LookupOut])
def list_disciplines(project: ProjectDep, db: DbSession) -> list[LookupOut]:
    rows = db.execute(
        select(Discipline).where(Discipline.project_id == project.id).order_by(Discipline.code)
    ).scalars()
    return [LookupOut.model_validate(r) for r in rows]


@router.post("/{project_id}/disciplines", response_model=LookupOut, status_code=201)
def create_discipline(project: ProjectDep, payload: LookupCreate, db: DbSession, user: CurrentUser) -> LookupOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    code = payload.code.strip().upper()
    if db.execute(
        select(Discipline).where(Discipline.project_id == project.id, Discipline.code == code)
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="این کد دیسیپلین قبلاً ثبت شده است.")
    row = Discipline(project_id=project.id, code=code, name=payload.name)
    db.add(row)
    db.commit()
    return LookupOut.model_validate(row)


@router.get("/{project_id}/units", response_model=list[LookupOut])
def list_units(project: ProjectDep, db: DbSession) -> list[LookupOut]:
    rows = db.execute(
        select(Unit).where(Unit.project_id == project.id).order_by(Unit.code)
    ).scalars()
    return [LookupOut.model_validate(r) for r in rows]


@router.post("/{project_id}/units", response_model=LookupOut, status_code=201)
def create_unit(project: ProjectDep, payload: LookupCreate, db: DbSession, user: CurrentUser) -> LookupOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    code = payload.code.strip().upper()
    if db.execute(select(Unit).where(Unit.project_id == project.id, Unit.code == code)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="این کد یونیت قبلاً ثبت شده است.")
    row = Unit(project_id=project.id, code=code, name=payload.name)
    db.add(row)
    db.commit()
    return LookupOut.model_validate(row)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(project: ProjectDep, db: DbSession) -> list[MemberOut]:
    rows = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project.id)
    ).scalars()
    return [MemberOut.model_validate(r) for r in rows]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(
    project: ProjectDep, payload: MemberCreate, db: DbSession, user: CurrentUser
) -> MemberOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")
    try:
        role = ProjectRole(payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="نقش نامعتبر است.") from exc
    existing = db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == payload.user_id,
            ProjectMember.role == role,
            ProjectMember.discipline_id == payload.discipline_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return MemberOut.model_validate(existing)
    member = ProjectMember(
        project_id=project.id, user_id=payload.user_id, role=role, discipline_id=payload.discipline_id
    )
    db.add(member)
    audit.record(
        db, action="project.member.add", entity_type="project_member", entity_id=member.id,
        actor=user, project_id=project.id, after={"user": target.email, "role": role.value},
    )
    db.commit()
    db.refresh(member)
    return MemberOut.model_validate(member)


@router.delete("/{project_id}/members/{member_id}", status_code=204)
def remove_member(
    project: ProjectDep, member_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> None:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    member = db.get(ProjectMember, member_id)
    if member is None or member.project_id != project.id:
        raise HTTPException(status_code=404, detail="عضو یافت نشد.")
    audit.record(
        db, action="project.member.remove", entity_type="project_member", entity_id=member.id,
        actor=user, project_id=project.id, before={"user_id": str(member.user_id)},
    )
    db.delete(member)
    db.commit()


# ---------------------------------------------------------------------------
# Title-block templates
# ---------------------------------------------------------------------------
@router.get("/{project_id}/title-block-templates", response_model=list[TitleBlockTemplateOut])
def list_templates(project: ProjectDep, db: DbSession) -> list[TitleBlockTemplateOut]:
    rows = db.execute(
        select(TitleBlockTemplate)
        .where(TitleBlockTemplate.project_id == project.id)
        .order_by(TitleBlockTemplate.priority, TitleBlockTemplate.name)
    ).scalars()
    return [TitleBlockTemplateOut.model_validate(r) for r in rows]


@router.post("/{project_id}/title-block-templates", response_model=TitleBlockTemplateOut, status_code=201)
def create_template(
    project: ProjectDep, payload: TitleBlockTemplateCreate, db: DbSession, user: CurrentUser
) -> TitleBlockTemplateOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    template = TitleBlockTemplate(
        project_id=project.id,
        name=payload.name,
        page_aspect_ratios=payload.page_aspect_ratios,
        regions=payload.regions,
        anchor_keywords=payload.anchor_keywords,
        field_regex=payload.field_regex,
        priority=payload.priority,
        created_by_id=user.id,
    )
    db.add(template)
    audit.record(
        db, action="title_block.create", entity_type="title_block_template",
        entity_id=template.id, actor=user, project_id=project.id, after={"name": payload.name},
    )
    db.commit()
    return TitleBlockTemplateOut.model_validate(template)


@router.patch("/{project_id}/title-block-templates/{template_id}", response_model=TitleBlockTemplateOut)
def update_template(
    project: ProjectDep,
    template_id: uuid.UUID,
    payload: TitleBlockTemplateUpdate,
    db: DbSession,
    user: CurrentUser,
) -> TitleBlockTemplateOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    template = db.get(TitleBlockTemplate, template_id)
    if template is None or template.project_id != project.id:
        raise HTTPException(status_code=404, detail="قالب یافت نشد.")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(template, field, value)
    template.version += 1
    audit.record(
        db, action="title_block.update", entity_type="title_block_template",
        entity_id=template.id, actor=user, project_id=project.id, after=updates,
    )
    db.commit()
    return TitleBlockTemplateOut.model_validate(template)


@router.get("/{project_id}/documents", summary="فهرست مدارک پروژه (MDL + وضعیت)")
def list_documents(
    project: ProjectDep,
    db: DbSession,
    q: str | None = None,
    discipline: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    stmt = select(MDLItem).where(MDLItem.project_id == project.id, MDLItem.is_active.is_(True))
    if q:
        like = f"%{q.strip().upper()}%"
        stmt = stmt.where(MDLItem.doc_code_norm.like(like) | func.upper(MDLItem.title).like(like))
    if discipline:
        stmt = stmt.join(Discipline, isouter=True).where(Discipline.code == discipline.upper())
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(stmt.order_by(MDLItem.doc_code_norm).limit(min(limit, 200)).offset(offset)).scalars()
    )
    revision_counts = dict(
        db.execute(
            select(DocumentRevision.mdl_item_id, func.count(DocumentRevision.id))
            .where(DocumentRevision.project_id == project.id)
            .group_by(DocumentRevision.mdl_item_id)
        ).all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": str(item.id),
                "doc_code": item.doc_code,
                "title": item.title,
                "current_rev": item.current_rev,
                "status": item.status,
                "discipline": item.discipline.code if item.discipline else None,
                "unit": item.unit.code if item.unit else None,
                "doc_type": item.doc_type,
                "due_date": item.due_date.isoformat() if item.due_date else None,
                "revision_count": revision_counts.get(item.id, 0),
            }
            for item in rows
        ],
    }
