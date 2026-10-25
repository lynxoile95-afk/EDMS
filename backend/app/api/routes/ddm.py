"""DDM (Document Distribution Matrix) management and simulation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.models import DDMRule, Discipline, Unit, User
from app.models.enums import ProjectRole
from app.schemas.transmittals import (
    DDMRuleCreate,
    DDMRuleOut,
    DDMSimulateOut,
    DDMSimulateRequest,
    RecipientOut,
)
from app.services import acl, audit, ddm

router = APIRouter(prefix="/projects/{project_id}/ddm", tags=["ddm"])


def _out(db, rule: DDMRule) -> DDMRuleOut:
    recipients = []
    for rec in rule.recipients:
        user = db.get(User, rec.user_id)
        recipients.append(
            RecipientOut(
                id=rec.id,
                user_id=rec.user_id,
                action=str(rec.action),
                ack_status="pending",
                full_name=user.full_name if user else None,
                email=user.email if user else None,
            )
        )
    return DDMRuleOut(
        id=rule.id,
        project_id=rule.project_id,
        name=rule.name,
        discipline_id=rule.discipline_id,
        unit_id=rule.unit_id,
        doc_type=rule.doc_type,
        purpose=str(rule.purpose) if rule.purpose else None,
        default_action=str(rule.default_action),
        due_days=rule.due_days,
        priority=rule.priority,
        is_active=rule.is_active,
        recipients=recipients,
    )


@router.get("/rules", response_model=list[DDMRuleOut], summary="فهرست قواعد توزیع")
def list_rules(project: ProjectDep, db: DbSession, user: CurrentUser) -> list[DDMRuleOut]:
    acl.require_project(db, user, project.id)
    rows = db.execute(
        select(DDMRule).where(DDMRule.project_id == project.id).order_by(DDMRule.priority, DDMRule.name)
    ).scalars()
    return [_out(db, rule) for rule in rows]


@router.post("/rules", response_model=DDMRuleOut, status_code=201, summary="افزودن قاعده DDM")
def create_rule(project: ProjectDep, payload: DDMRuleCreate, db: DbSession, user: CurrentUser) -> DDMRuleOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    if not payload.recipients:
        raise HTTPException(status_code=422, detail="قاعده بدون گیرنده معتبر نیست.")
    rule = DDMRule(
        project_id=project.id,
        name=payload.name,
        discipline_id=payload.discipline_id,
        unit_id=payload.unit_id,
        doc_type=payload.doc_type,
        purpose=payload.purpose,
        default_action=payload.default_action,
        due_days=payload.due_days,
        priority=payload.priority,
        is_active=payload.is_active,
    )
    db.add(rule)
    db.flush()
    ddm.replace_recipients(db, rule, payload.recipients)
    audit.record(
        db, action="ddm.rule.create", entity_type="ddm_rule", entity_id=rule.id, actor=user,
        project_id=project.id, after={"name": rule.name, "recipients": len(payload.recipients)},
    )
    db.commit()
    db.refresh(rule)
    return _out(db, rule)


@router.patch("/rules/{rule_id}", response_model=DDMRuleOut, summary="ویرایش قاعده DDM")
def update_rule(
    project: ProjectDep, rule_id: uuid.UUID, payload: DDMRuleCreate, db: DbSession, user: CurrentUser
) -> DDMRuleOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    rule = db.get(DDMRule, rule_id)
    if rule is None or rule.project_id != project.id:
        raise HTTPException(status_code=404, detail="قاعده یافت نشد.")
    for field, value in payload.model_dump(exclude={"recipients"}).items():
        setattr(rule, field, value)
    ddm.replace_recipients(db, rule, payload.recipients)
    audit.record(
        db, action="ddm.rule.update", entity_type="ddm_rule", entity_id=rule.id, actor=user,
        project_id=project.id,
    )
    db.commit()
    db.refresh(rule)
    return _out(db, rule)


@router.delete("/rules/{rule_id}", status_code=204, response_model=None, summary="حذف قاعده DDM")
def delete_rule(project: ProjectDep, rule_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    rule = db.get(DDMRule, rule_id)
    if rule is None or rule.project_id != project.id:
        raise HTTPException(status_code=404, detail="قاعده یافت نشد.")
    audit.record(
        db, action="ddm.rule.delete", entity_type="ddm_rule", entity_id=rule.id, actor=user,
        project_id=project.id, before={"name": rule.name},
    )
    db.delete(rule)
    db.commit()


@router.post("/simulate", response_model=DDMSimulateOut, summary="شبیه‌سازی توزیع یک مدرک")
def simulate(
    project: ProjectDep, payload: DDMSimulateRequest, db: DbSession, user: CurrentUser
) -> DDMSimulateOut:
    acl.require_project(db, user, project.id)
    discipline = None
    if payload.discipline_code:
        discipline = db.execute(
            select(Discipline).where(
                Discipline.project_id == project.id, Discipline.code == payload.discipline_code.upper()
            )
        ).scalar_one_or_none()
    unit = None
    if payload.unit_code:
        unit = db.execute(
            select(Unit).where(Unit.project_id == project.id, Unit.code == payload.unit_code.upper())
        ).scalar_one_or_none()
    resolution = ddm.resolve(
        db,
        project_id=project.id,
        discipline_id=discipline.id if discipline else None,
        unit_id=unit.id if unit else None,
        doc_type=payload.doc_type,
        purpose=payload.purpose,
        settings=project.settings,
    )
    return DDMSimulateOut(
        matched=resolution.matched,
        rule_name=resolution.rule_name,
        due_days=resolution.due_days,
        explanation=resolution.explanation,
        recipients=[
            {"user_id": str(r.user_id), "action": str(r.action), "full_name": r.full_name}
            for r in resolution.recipients
        ],
    )
