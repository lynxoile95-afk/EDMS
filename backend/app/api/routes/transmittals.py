"""Transmittal endpoints: create, preview, issue, acknowledge, cancel, remind."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, or_, select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.models import (
    DocumentRevision,
    Project,
    Transmittal,
    TransmittalLine,
    TransmittalRecipient,
    User,
)
from app.models.enums import ProjectRole, TransmittalStatus, TransmittalType
from app.schemas.transmittals import (
    AcknowledgeRequest,
    CancelRequest,
    CompleteRequest,
    ForwardRequest,
    IssueRequest,
    LineOut,
    RecipientOut,
    ReferRequest,
    ReferralOut,
    TransmittalCreate,
    TransmittalDetail,
    TransmittalOut,
)
from app.services import acl, audit, notification, numbering, routing, transmittal_service
from app.services.project_settings import merge_settings
from app.services.storage import get_storage
from app.utils.jalali import format_jalali

router = APIRouter(tags=["transmittals"])


def _out(transmittal: Transmittal) -> TransmittalOut:
    acknowledged = sum(
        1 for r in transmittal.recipients if str(r.ack_status) == "acknowledged"
    )
    return TransmittalOut(
        id=transmittal.id,
        project_id=transmittal.project_id,
        number=transmittal.number,
        type=str(transmittal.type),
        purpose=str(transmittal.purpose),
        status=str(transmittal.status),
        from_org=transmittal.from_org,
        to_org=transmittal.to_org,
        due_date=transmittal.due_date,
        issued_at=transmittal.issued_at,
        remarks=transmittal.remarks,
        meta={
            **(transmittal.meta or {}),
            "due_date_jalali": format_jalali(transmittal.due_date) if transmittal.due_date else None,
            "issued_at_jalali": format_jalali(transmittal.issued_at) if transmittal.issued_at else None,
        },
        stage=str(transmittal.stage or "draft"),
        created_at=transmittal.created_at,
        line_count=len(transmittal.lines),
        recipient_count=len(transmittal.recipients),
        acknowledged_count=acknowledged,
    )


def _referral_out(referral) -> ReferralOut:
    return ReferralOut(
        id=referral.id,
        transmittal_id=referral.transmittal_id,
        from_user_id=referral.from_user_id,
        from_user_name=referral.from_user.full_name if referral.from_user else None,
        to_user_id=referral.to_user_id,
        to_user_name=referral.to_user.full_name if referral.to_user else None,
        discipline_id=referral.discipline_id,
        discipline_code=referral.discipline.code if referral.discipline else None,
        action=str(referral.action),
        note=referral.note,
        due_date=referral.due_date,
        status=str(referral.status),
        responded_at=referral.responded_at,
        response_note=referral.response_note,
        created_at=referral.created_at,
    )


def _detail(db, transmittal: Transmittal) -> TransmittalDetail:
    base = _out(transmittal)
    lines = [
        LineOut(
            id=line.id,
            line_no=line.line_no,
            revision_id=line.revision_id,
            action=str(line.action),
            status=str(line.status),
            due_date=line.due_date,
            note=line.note,
            doc_code=line.revision.mdl_item.doc_code if line.revision else None,
            title=line.revision.mdl_item.title if line.revision else None,
            rev=line.revision.rev if line.revision else None,
            page_count=line.revision.page_count if line.revision else None,
            discipline_code=line.discipline_code,
        )
        for line in transmittal.lines
    ]
    recipients = [
        RecipientOut(
            id=recipient.id,
            user_id=recipient.user_id,
            action=str(recipient.action),
            ack_status=str(recipient.ack_status),
            acknowledged_at=recipient.acknowledged_at,
            full_name=recipient.user.full_name if recipient.user else None,
            email=recipient.user.email if recipient.user else None,
        )
        for recipient in transmittal.recipients
    ]
    try:
        project = db.get(Project, transmittal.project_id)
        settings = merge_settings(project.settings if project else None)
        preview = numbering.peek_temp(db, project, settings=settings) if project else None
    except Exception:  # noqa: BLE001
        preview = None
    pdf_url = None
    if transmittal.pdf_key:
        try:
            pdf_url = get_storage().presigned_url(transmittal.pdf_key)
        except Exception:  # noqa: BLE001
            pdf_url = None
    return TransmittalDetail(
        **base.model_dump(),
        lines=lines,
        recipients=recipients,
        referrals=[_referral_out(item) for item in transmittal.referrals],
        pdf_url=pdf_url,
        preview_number=None if transmittal.number else preview,
        stage_label=routing.STAGE_LABELS.get(str(transmittal.stage)),
    )


@router.get("/projects/{project_id}/transmittals", summary="فهرست برگه‌های ارسال")
def list_transmittals(
    project: ProjectDep,
    db: DbSession,
    user: CurrentUser,
    status: str | None = Query(None),
    purpose: str | None = Query(None),
    q: str | None = Query(None),
    mine: bool = Query(False, description="فقط برگه‌هایی که در کارتابل من هستند"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> dict:
    stmt = select(Transmittal).where(Transmittal.project_id == project.id)
    if mine:
        # Each desk is defined by the route, not by "everything I can see":
        #   contractor  → the drafts it still owns
        #   document control → sheets on its desk (draft / dcc)
        #   management  → sheets waiting for a referral decision
        #   discipline  → only the referrals addressed to me
        clauses = [Transmittal.id.in_(routing.pending_for_user(db, project.id, user.id))]
        if acl.has_role(db, user, project.id, ProjectRole.SUBMITTER):
            clauses.append(
                and_(Transmittal.created_by_id == user.id, Transmittal.stage == "draft")
            )
        if acl.has_role(db, user, project.id, ProjectRole.DCC, ProjectRole.SYSTEM_ADMIN):
            clauses.append(Transmittal.stage.in_(["draft", "dcc"]))
        if acl.has_role(
            db,
            user,
            project.id,
            ProjectRole.ENGINEERING_MANAGER,
            ProjectRole.PROJECT_MANAGER,
            ProjectRole.SYSTEM_ADMIN,
        ):
            clauses.append(Transmittal.stage == "management")
        stmt = stmt.where(or_(*clauses))
    if status:
        stmt = stmt.where(Transmittal.status == status)
    if purpose:
        stmt = stmt.where(Transmittal.purpose == purpose)
    if q:
        stmt = stmt.where(Transmittal.number.ilike(f"%{q.strip()}%"))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(stmt.order_by(Transmittal.created_at.desc()).offset((page - 1) * size).limit(size)).scalars()
    )
    return {"total": total, "items": [_out(row).model_dump() for row in rows]}


@router.post(
    "/projects/{project_id}/transmittals",
    response_model=TransmittalDetail,
    summary="ساخت پیش‌نویس برگه ارسال از مدارک ثبت‌شده",
)
def create_transmittal(
    project: ProjectDep, payload: TransmittalCreate, db: DbSession, user: CurrentUser
) -> TransmittalDetail:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    revisions = list(
        db.execute(
            select(DocumentRevision).where(
                DocumentRevision.id.in_(payload.revision_ids),
                DocumentRevision.project_id == project.id,
            )
        ).scalars()
    )
    if len(revisions) != len(set(payload.revision_ids)):
        raise HTTPException(status_code=422, detail="برخی از مدارک انتخاب‌شده در این پروژه یافت نشدند.")

    drafts = transmittal_service.create_draft_transmittals(
        db,
        project=project,
        revisions=revisions,
        purpose=payload.purpose,  # type: ignore[arg-type]
        transmittal_type=TransmittalType(payload.type),
        actor=user,
        remarks=payload.remarks,
    )
    for draft in drafts:
        existing = {r.user_id for r in draft.recipients}
        for recipient_id in payload.extra_recipient_ids:
            if recipient_id not in existing:
                from app.models.enums import ActionRequired

                db.add(
                    TransmittalRecipient(
                        transmittal_id=draft.id,
                        user_id=recipient_id,
                        action=ActionRequired.INFO,
                    )
                )
    db.commit()
    db.refresh(drafts[0])
    return _detail(db, drafts[0])


# ---------------------------------------------------------------------------
# Review route: contractor → document control → management → discipline
# ---------------------------------------------------------------------------


def _routing_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get(
    "/transmittals/{transmittal_id}/referrals",
    response_model=list[ReferralOut],
    summary="گردش مدرک — تاریخچه ارجاع‌ها",
)
def list_referrals(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[ReferralOut]:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(db, user, transmittal.project_id)
    return [_referral_out(item) for item in transmittal.referrals]


@router.post(
    "/transmittals/{transmittal_id}/forward",
    response_model=TransmittalDetail,
    summary="ارجاع از کنترل اسناد به مدیر مهندسی / مدیر پروژه",
)
def forward(
    transmittal_id: uuid.UUID,
    payload: ForwardRequest,
    db: DbSession,
    user: CurrentUser,
) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(
        db, user, transmittal.project_id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER]
    )
    try:
        routing.forward_to_management(
            db,
            transmittal=transmittal,
            actor=user,
            to_user_id=payload.to_user_id,
            note=payload.note,
            due_days=payload.due_days,
        )
        db.commit()
    except routing.RoutingError as exc:
        db.rollback()
        raise _routing_error(exc) from exc
    db.refresh(transmittal)
    return _detail(db, transmittal)


@router.post(
    "/transmittals/{transmittal_id}/refer",
    response_model=TransmittalDetail,
    summary="ارجاع از مدیریت به کارشناس دیسیپلین",
)
def refer(
    transmittal_id: uuid.UUID,
    payload: ReferRequest,
    db: DbSession,
    user: CurrentUser,
) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(
        db,
        user,
        transmittal.project_id,
        [ProjectRole.ENGINEERING_MANAGER, ProjectRole.PROJECT_MANAGER],
    )
    try:
        routing.refer_to_discipline(
            db,
            transmittal=transmittal,
            actor=user,
            discipline_id=payload.discipline_id,
            to_user_id=payload.to_user_id,
            action=payload.action,
            note=payload.note,
            due_days=payload.due_days,
        )
        db.commit()
    except routing.RoutingError as exc:
        db.rollback()
        raise _routing_error(exc) from exc
    db.refresh(transmittal)
    return _detail(db, transmittal)


@router.post(
    "/transmittals/{transmittal_id}/referrals/{referral_id}/complete",
    response_model=TransmittalDetail,
    summary="پایان بررسی کارشناس دیسیپلین و بازگشت به مدیریت",
)
def complete_referral(
    transmittal_id: uuid.UUID,
    referral_id: uuid.UUID,
    payload: CompleteRequest,
    db: DbSession,
    user: CurrentUser,
) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(db, user, transmittal.project_id)
    referral = next((item for item in transmittal.referrals if item.id == referral_id), None)
    if referral is None:
        raise HTTPException(status_code=404, detail="ارجاع یافت نشد.")
    if referral.to_user_id != user.id and not acl.has_role(
        db, user, transmittal.project_id, ProjectRole.ENGINEERING_MANAGER, ProjectRole.PROJECT_MANAGER
    ):
        raise HTTPException(status_code=403, detail="فقط گیرنده ارجاع می‌تواند آن را ببندد.")
    try:
        routing.complete_referral(
            db, transmittal=transmittal, referral=referral, actor=user, note=payload.note
        )
        db.commit()
    except routing.RoutingError as exc:
        db.rollback()
        raise _routing_error(exc) from exc
    db.refresh(transmittal)
    return _detail(db, transmittal)


@router.get("/transmittals/{transmittal_id}", response_model=TransmittalDetail, summary="جزئیات برگه ارسال")
def get_transmittal(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(db, user, transmittal.project_id)
    return _detail(db, transmittal)


@router.post(
    "/transmittals/{transmittal_id}/issue",
    response_model=TransmittalDetail,
    summary="صدور نهایی (تخصیص شماره، تولید PDF، ارسال اعلان)",
)
def issue_transmittal(
    transmittal_id: uuid.UUID, payload: IssueRequest, db: DbSession, user: CurrentUser
) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    project = acl.require_project(db, user, transmittal.project_id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    if payload.remarks:
        transmittal.remarks = payload.remarks
    try:
        transmittal_service.issue_transmittal(
            db, transmittal=transmittal, actor=user, storage=get_storage()
        )
        # the sheet now waits on document control, not on the contractor
        routing.mark_issued(db, transmittal)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - PDF/storage failure must roll back
        db.rollback()
        raise HTTPException(status_code=500, detail=f"صدور برگه ارسال ناموفق بود: {exc}") from exc

    if payload.notify:
        try:
            from app.workers.tasks import deliver_notifications

            deliver_notifications.delay()
        except Exception:  # noqa: BLE001
            pass
    db.refresh(transmittal)
    return _detail(db, transmittal)


@router.post("/transmittals/{transmittal_id}/acknowledge", response_model=TransmittalDetail)
def acknowledge(
    transmittal_id: uuid.UUID, payload: AcknowledgeRequest, db: DbSession, user: CurrentUser
) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(db, user, transmittal.project_id)
    try:
        transmittal_service.acknowledge(db, transmittal=transmittal, user=user, note=payload.note)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _detail(db, transmittal)


@router.post("/transmittals/{transmittal_id}/cancel", response_model=TransmittalDetail)
def cancel(
    transmittal_id: uuid.UUID, payload: CancelRequest, db: DbSession, user: CurrentUser
) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(db, user, transmittal.project_id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    try:
        transmittal_service.cancel_transmittal(db, transmittal=transmittal, actor=user, reason=payload.reason)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _detail(db, transmittal)


@router.get("/transmittals/{transmittal_id}/pdf", summary="دریافت PDF برگه ارسال")
def download_pdf(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser):
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None or not transmittal.pdf_key:
        raise HTTPException(status_code=404, detail="فایل PDF برای این برگه ارسال موجود نیست.")
    acl.require_project(db, user, transmittal.project_id)
    url = get_storage().presigned_url(
        transmittal.pdf_key, filename=f"{transmittal.number or 'draft'}.pdf"
    )
    audit.record(
        db, action="transmittal.download", entity_type="transmittal", entity_id=transmittal.id,
        actor=user, project_id=transmittal.project_id,
    )
    db.commit()
    return RedirectResponse(url=url, status_code=302)


@router.post("/transmittals/{transmittal_id}/remind", summary="ارسال یادآور به گیرندگان")
def remind(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_project(db, user, transmittal.project_id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    if transmittal.status not in {TransmittalStatus.ISSUED, TransmittalStatus.PARTIALLY_ACKNOWLEDGED}:
        raise HTTPException(status_code=422, detail="یادآور فقط برای برگه‌های صادرشده ارسال می‌شود.")
    sent = 0
    for recipient in transmittal.recipients:
        if str(recipient.ack_status) != "pending":
            continue
        notification.queue(
            db,
            user=recipient.user,
            template="transmittal.reminder",
            context={
                "number": transmittal.number,
                "due_date": format_jalali(transmittal.due_date),
                "link": f"{settings_public_base()}/transmittals/{transmittal.id}",
            },
            project_id=transmittal.project_id,
            entity_type="transmittal",
            entity_id=str(transmittal.id),
            dedupe_key=f"remind:{transmittal.id}:{recipient.user_id}:{datetime.now(timezone.utc):%Y%m%d}",
        )
        sent += 1
    audit.record(
        db, action="transmittal.remind", entity_type="transmittal", entity_id=transmittal.id,
        actor=user, project_id=transmittal.project_id, after={"count": sent},
    )
    db.commit()
    return {"queued": sent}


def settings_public_base() -> str:
    from app.core.config import settings

    return settings.public_base_url
