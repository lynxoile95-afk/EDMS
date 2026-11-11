"""Transmittal endpoints: create, preview, issue, acknowledge, cancel, remind."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, or_, select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.core.config import settings
from app.models import (
    DocumentRevision,
    ExtractionResult,
    Project,
    ProjectMember,
    Transmittal,
    TransmittalLine,
    TransmittalRecipient,
    TransmittalReferral,
    User,
)
from app.models.enums import (
    ActionRequired,
    ProjectRole,
    ReferralStatus,
    TransmittalStatus,
    TransmittalType,
)
from app.schemas.transmittals import (
    AcknowledgeRequest,
    AttachmentAccessUpdate,
    CancelRequest,
    CompleteRequest,
    ForwardRequest,
    IssueRequest,
    LineOut,
    PersonnelReferralItem,
    RecipientOut,
    ReferPersonnelRequest,
    ReferRequest,
    AttachmentOut,
    ReferralOut,
    TransmittalCreate,
    TransmittalDetail,
    TransmittalOut,
)
from app.services import acl, audit, notification, numbering, routing, transmittal_service
from app.services.project_settings import merge_settings
from app.services.storage import build_key, get_storage
from app.utils.jalali import format_jalali

router = APIRouter(tags=["transmittals"])

_FINALIZED_ATTACHMENT_STATUSES = {
    TransmittalStatus.ISSUED.value,
    TransmittalStatus.PARTIALLY_ACKNOWLEDGED.value,
    TransmittalStatus.ACKNOWLEDGED.value,
    TransmittalStatus.CLOSED.value,
    TransmittalStatus.CANCELLED.value,
}


def _assert_attachments_editable(transmittal: Transmittal) -> None:
    """Lock attachment bytes and metadata after final issue or cancellation.

    The frontend hides the editing controls as a convenience, but this check is
    deliberately server-side too: a stale tab, direct API client, project admin,
    or superuser must not be able to alter the immutable finalized record.
    """
    status_value = getattr(transmittal.status, "value", transmittal.status)
    if str(status_value) in _FINALIZED_ATTACHMENT_STATUSES or transmittal.number:
        raise HTTPException(
            status_code=409,
            detail="پس از صدور قطعی ترانسمیتال، افزودن، ویرایش یا حذف فایل پیوست مجاز نیست.",
        )


def _out(transmittal: Transmittal) -> TransmittalOut:
    acknowledged = sum(
        1 for r in transmittal.recipients if str(r.ack_status) == "acknowledged"
    )
    first_line = transmittal.lines[0] if transmittal.lines else None
    first_revision = first_line.revision if first_line is not None else None
    first_mdl_item = first_revision.mdl_item if first_revision is not None else None
    return TransmittalOut(
        id=transmittal.id,
        project_id=transmittal.project_id,
        access_circle_id=transmittal.access_circle_id,
        access_circle_name=transmittal.access_circle.name if transmittal.access_circle else None,
        number=transmittal.number,
        type=str(transmittal.type),
        purpose=str(transmittal.purpose),
        status=str(transmittal.status),
        from_org=transmittal.from_org,
        to_org=transmittal.to_org,
        due_date=transmittal.due_date,
        issue_date=transmittal.issue_date,
        issued_at=transmittal.issued_at,
        created_by_id=transmittal.created_by_id,
        created_by_name=transmittal.created_by.full_name if transmittal.created_by else None,
        issued_by_id=transmittal.issued_by_id,
        issued_by_name=transmittal.issued_by.full_name if transmittal.issued_by else None,
        final_approver_id=transmittal.final_approver_id,
        final_approver_name=transmittal.final_approver.full_name if transmittal.final_approver else None,
        manual_workflow=bool(transmittal.manual_workflow),
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
        document_code=first_mdl_item.doc_code if first_mdl_item is not None else None,
        document_title=first_mdl_item.title if first_mdl_item is not None else None,
        document_type=first_mdl_item.doc_type if first_mdl_item is not None else None,
        line_action=str(first_line.action) if first_line is not None else None,
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


def _attachment_out(raw: dict) -> AttachmentOut:
    access_user_ids = [str(value) for value in (raw.get("access_user_ids") or [])]
    return AttachmentOut(
        id=raw["id"],
        line_id=raw["line_id"],
        revision_id=raw.get("revision_id"),
        kind=raw["kind"],
        filename=raw["filename"],
        content_type=raw.get("content_type") or "application/octet-stream",
        size_bytes=int(raw.get("size_bytes") or 0),
        uploaded_at=raw["uploaded_at"],
        uploaded_by_id=raw.get("uploaded_by_id"),
        uploaded_by_name=raw.get("uploaded_by_name"),
        access_user_ids=access_user_ids,
        access_restricted=raw.get("kind") == "native" and bool(access_user_ids),
    )


def _attachment_access_ids(raw: dict) -> set[str]:
    return {str(value) for value in (raw.get("access_user_ids") or [])}


def _native_attachment_accessible(db, user: User, transmittal: Transmittal, attachment: dict) -> bool:
    """Native files may be narrower than the transmittal's general ACL.

    Legacy attachments without an explicit list continue to use the transmittal
    access-circle rule. New native files get an explicit list at upload time;
    the uploader and the transmittal's issuer/creator remain emergency owners so
    an access mistake cannot lock the source file away from its custodian.
    """
    if attachment.get("kind") != "native":
        return True
    access_ids = _attachment_access_ids(attachment)
    if not access_ids or user.is_superuser:
        return True
    owner_ids = {
        str(value)
        for value in (
            attachment.get("uploaded_by_id"),
            transmittal.created_by_id,
            transmittal.sender_id,
            transmittal.issued_by_id,
        )
        if value
    }
    return str(user.id) in access_ids or str(user.id) in owner_ids


def _assert_native_attachment_access(db, user: User, transmittal: Transmittal, attachment: dict) -> None:
    if not _native_attachment_accessible(db, user, transmittal, attachment):
        raise acl.AccessDenied("دسترسی مشاهده یا دریافت این فایل Native برای شما مجاز نیست.")


def _can_manage_attachments(db, user: User, transmittal: Transmittal) -> bool:
    if acl.has_role(
        db,
        user,
        transmittal.project_id,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
        ProjectRole.SYSTEM_ADMIN,
    ):
        return True
    return bool(
        transmittal.manual_workflow
        and transmittal.created_by_id == user.id
        and str(transmittal.status) == str(TransmittalStatus.DRAFT)
    )


def _is_transmittal_issuer(user: User, transmittal: Transmittal) -> bool:
    return user.is_superuser or user.id in {
        transmittal.created_by_id,
        transmittal.sender_id,
        transmittal.issued_by_id,
    }


def _detail(db, transmittal: Transmittal) -> TransmittalDetail:
    base = _out(transmittal)
    project = db.get(Project, transmittal.project_id)
    project_meta: dict[str, object] = {}
    preview = None
    if project is not None:
        project_meta = {
            "contract_number": project.code,
            "contractor_code": project.contractor_code,
            "parent_company_code": project.parent_company_code,
        }
        type_value = getattr(transmittal.type, "value", transmittal.type)
        if str(type_value).upper() in {"OT", "OS"}:
            missing = numbering.missing_company_codes(project)
            if missing:
                project_meta["numbering_warning"] = (
                    "کد " + " و ".join(missing) +
                    " پروژه ثبت نشده است؛ شماره با قالب قدیمی صادر می‌شود و برای فعال شدن قالب جدید، "
                    "هر دو کد را دقیقاً با دو حرف انگلیسی A-Z تکمیل کنید."
                )
        try:
            settings = merge_settings(project.settings)
            preview = numbering.peek_temp(
                db,
                project,
                transmittal_type=str(type_value),
                settings=settings,
            )
        except Exception:  # noqa: BLE001
            # A legacy project without company codes still needs to open so the
            # administrator can edit it; the warning above explains the issue.
            preview = None
        base.meta = {**(base.meta or {}), **project_meta}
    result_ids = {
        line.revision.extraction_result_id
        for line in transmittal.lines
        if line.revision is not None and line.revision.extraction_result_id is not None
    }
    result_statuses = {
        result.id: result.doc_status
        for result in db.execute(
            select(ExtractionResult).where(ExtractionResult.id.in_(result_ids))
        ).scalars()
    } if result_ids else {}
    lines = [
        LineOut(
            id=line.id,
            line_no=line.line_no,
            revision_id=line.revision_id,
            mdl_item_id=line.revision.mdl_item.id if line.revision and line.revision.mdl_item else None,
            action=str(line.action),
            status=str(line.status),
            doc_status=(
                line.doc_status
                or (
                    line.revision.mdl_item.status
                    if line.revision and line.revision.mdl_item and line.revision.mdl_item.status
                    else result_statuses.get(line.revision.extraction_result_id)
                    if line.revision and line.revision.extraction_result_id
                    else None
                )
            ),
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
        attachments=[_attachment_out(item) for item in (transmittal.meta or {}).get("attachments", [])],
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
    stmt = select(Transmittal).where(
        Transmittal.project_id == project.id,
        acl.transmittal_visibility_clause(db, user),
    )
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
        # A manual creator keeps the sheet in their own desk after final issue so
        # the separate «ارجاع مدرک» operation remains available there.
        clauses.append(
            and_(Transmittal.manual_workflow.is_(True), Transmittal.created_by_id == user.id)
        )
        if acl.has_role(db, user, project.id, ProjectRole.DCC, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.SYSTEM_ADMIN):
            clauses.append(Transmittal.stage.in_(["draft", "dcc"]))
        if acl.has_role(
            db,
            user,
            project.id,
            ProjectRole.ENGINEERING_MANAGER,
            ProjectRole.PROJECT_CONTROL,
            ProjectRole.PMO,
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
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.PROJECT_MANAGER])
    acl.validate_circle_for_transmittal(db, user, project.id, payload.access_circle_id)
    if payload.type == TransmittalType.EXTERNAL.value and not acl.has_external_access(db, user, project.id):
        raise acl.AccessDenied("صدور ترانسمیتال خارجی فقط برای عضو مجاز به دسترسی خارج از حلقه امکان‌پذیر است.")
    acl.assert_recipients_allowed(
        db, user, project.id, payload.extra_recipient_ids, payload.access_circle_id
    )
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
        access_circle_id=payload.access_circle_id,
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
        acl.assert_recipients_allowed(
            db,
            user,
            project.id,
            [recipient.user_id for recipient in draft.recipients] + payload.extra_recipient_ids,
            payload.access_circle_id,
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
    acl.require_transmittal(db, user, transmittal)
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
    acl.require_transmittal(
        db, user, transmittal, [ProjectRole.DCC, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.PROJECT_MANAGER]
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
    acl.require_transmittal(
        db,
        user,
        transmittal,
        [ProjectRole.ENGINEERING_MANAGER, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.PROJECT_MANAGER],
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
    acl.require_transmittal(db, user, transmittal)
    referral = next((item for item in transmittal.referrals if item.id == referral_id), None)
    if referral is None:
        raise HTTPException(status_code=404, detail="ارجاع یافت نشد.")
    if referral.to_user_id != user.id and not acl.has_role(
        db, user, transmittal.project_id, ProjectRole.ENGINEERING_MANAGER, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.PROJECT_MANAGER
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
    acl.require_transmittal(db, user, transmittal)
    return _detail(db, transmittal)


@router.post(
    "/transmittals/{transmittal_id}/refer-people",
    response_model=TransmittalDetail,
    summary="ارجاع برگه ارسال به پرسنل انتخاب‌شده",
)
def refer_people(
    transmittal_id: uuid.UUID,
    payload: ReferPersonnelRequest,
    db: DbSession,
    user: CurrentUser,
) -> TransmittalDetail:
    """Send an issued transmittal to selected project personnel.

    Any active project member who can access the transmittal may initiate the
    referral. The selected project members still pass through the normal ACL and
    access-circle checks, and each person receives an independent referral row
    and notification.
    """
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_transmittal(db, user, transmittal)

    if str(transmittal.status) not in {
        str(TransmittalStatus.ISSUED),
        str(TransmittalStatus.PARTIALLY_ACKNOWLEDGED),
        str(TransmittalStatus.ACKNOWLEDGED),
        str(TransmittalStatus.CLOSED),
    }:
        raise HTTPException(
            status_code=409,
            detail="ارجاع مدرک پس از صدور نهایی ترانسمیتال امکان‌پذیر است.",
        )

    referral_specs = payload.people or [
        PersonnelReferralItem(
            user_id=user_id,
            action=ActionRequired.REVIEW,
            note=payload.note,
        )
        for user_id in (payload.user_ids or [])
    ]
    if not referral_specs:
        raise HTTPException(status_code=422, detail="حداقل یک عضو فعال پروژه را انتخاب کنید.")
    if payload.people and len({item.user_id for item in payload.people}) != len(payload.people):
        raise HTTPException(status_code=422, detail="هر عضو پروژه فقط یک‌بار قابل انتخاب است.")

    target_ids = {item.user_id for item in referral_specs}
    members = list(
        db.execute(
            select(ProjectMember)
            .join(User, User.id == ProjectMember.user_id)
            .where(
                ProjectMember.project_id == transmittal.project_id,
                ProjectMember.user_id.in_(target_ids),
                User.is_active.is_(True),
            )
        ).scalars()
    )
    active_members = {member.user_id: member for member in members}
    if set(active_members) != target_ids:
        raise HTTPException(
            status_code=422,
            detail="همه افراد انتخاب‌شده باید عضو فعال همین پروژه باشند.",
        )

    existing_pending = {
        referral.to_user_id
        for referral in transmittal.referrals
        if str(referral.status) == str(ReferralStatus.PENDING)
    }
    due_date = date.today() + timedelta(days=payload.due_days)
    created_count = 0
    created_people: list[dict[str, str | None]] = []
    for spec in referral_specs:
        member = active_members[spec.user_id]
        if member.user_id in existing_pending:
            continue
        target = member.user
        if target is None:
            continue
        acl.assert_can_route_to(db, user, target, transmittal)
        referral = TransmittalReferral(
            transmittal_id=transmittal.id,
            from_user_id=user.id,
            to_user_id=target.id,
            discipline_id=member.discipline_id,
            action=str(spec.action),
            note=spec.note,
            due_date=due_date,
            status=str(ReferralStatus.PENDING),
        )
        db.add(referral)
        db.flush()

        if not any(recipient.user_id == target.id for recipient in transmittal.recipients):
            transmittal.recipients.append(
                TransmittalRecipient(
                    user_id=target.id,
                    action=spec.action,
                )
            )

        notification.queue(
            db,
            user=target,
            template="transmittal.referred",
            context={
                "number": transmittal.number or "پیش‌نویس",
                "project": transmittal.meta.get("project_name", ""),
                "from": user.full_name,
                "action_fa": "ارجاع مدرک",
                "note": spec.note or "—",
                "link": f"{settings.public_base_url}/transmittals/{transmittal.id}",
            },
            project_id=transmittal.project_id,
            entity_type="transmittal",
            entity_id=str(transmittal.id),
            dedupe_key=f"manual-referral:{referral.id}:{target.id}",
        )
        created_people.append(
            {"user_id": str(target.id), "action": str(spec.action), "note": spec.note}
        )
        created_count += 1

    if created_count:
        transmittal.stage = "discipline"
    audit.record(
        db,
        action="transmittal.manual_refer",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=user,
        project_id=transmittal.project_id,
        after={"people": created_people, "created": created_count},
    )
    db.commit()
    db.refresh(transmittal)
    return _detail(db, transmittal)


@router.post(
    "/transmittals/{transmittal_id}/attachments",
    status_code=201,
    summary="پیوست PDF یا فایل Native به مدرک",
)
async def upload_attachment(
    transmittal_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    line_id: uuid.UUID = Form(...),
    kind: str = Form(..., description="pdf یا native"),
    file: UploadFile = File(...),
) -> dict:
    """Store a document attachment beside the transmittal line.

    Attachments intentionally live in the transmittal metadata because they are
    supplementary files, not registered PDF revisions. The actual bytes still go
    through the configured local/S3 storage abstraction and every operation is
    audited.
    """
    if kind not in {"pdf", "native"}:
        raise HTTPException(status_code=422, detail="نوع پیوست باید pdf یا native باشد.")
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    project = acl.require_transmittal(db, user, transmittal)
    _assert_attachments_editable(transmittal)
    if not _can_manage_attachments(db, user, transmittal):
        raise acl.AccessDenied("فقط مسئول کنترل یا ایجادکننده مجاز به بارگذاری پیوست است.")
    if kind == "native" and not _is_transmittal_issuer(user, transmittal):
        raise acl.AccessDenied("بارگذاری فایل Native فقط توسط صادرکننده ترانسمیتال مجاز است.")
    line = db.get(TransmittalLine, line_id)
    if line is None or line.transmittal_id != transmittal.id:
        raise HTTPException(status_code=404, detail="ردیف مدرک این برگه ارسال یافت نشد.")

    filename = os.path.basename((file.filename or "attachment").strip())[:500] or "attachment"
    max_bytes = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    head = b""
    suffix = os.path.splitext(filename)[1] or ".bin"
    temp_path = ""
    storage_key = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="edms-attachment-", suffix=suffix, delete=False) as temp:
            temp_path = temp.name
            while chunk := await file.read(1024 * 1024):
                if not head:
                    head = chunk[:8]
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=422,
                        detail=f"حجم فایل بیشتر از حد مجاز ({settings.max_upload_mb} مگابایت) است.",
                    )
                digest.update(chunk)
                temp.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="فایل پیوست خالی است.")
        if kind == "pdf" and not head.startswith(b"%PDF-"):
            raise HTTPException(status_code=422, detail="پیوست PDF معتبر نیست.")

        attachment_id = uuid.uuid4()
        storage_key = build_key(
            project.code,
            "transmittals",
            str(transmittal.id),
            "attachments",
            f"{attachment_id}{suffix.lower()}",
        )
        detected_type = mimetypes.guess_type(filename)[0]
        content_type = file.content_type or detected_type or "application/octet-stream"
        if content_type == "application/octet-stream" and detected_type:
            content_type = detected_type
        get_storage().put_file(storage_key, temp_path, content_type=content_type)
        attachment = {
            "id": str(attachment_id),
            "line_id": str(line.id),
            "revision_id": str(line.revision_id),
            "kind": kind,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "storage_key": storage_key,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "uploaded_by_id": str(user.id),
            "uploaded_by_name": user.full_name,
            # Native files start private to their issuer. The issuer can grant
            # access explicitly through the Set Access action in the table.
            "access_user_ids": [str(user.id)] if kind == "native" else [],
        }
        meta = dict(transmittal.meta or {})
        attachments = list(meta.get("attachments") or [])
        attachments.append(attachment)
        transmittal.meta = {**meta, "attachments": attachments}
        audit.record(
            db,
            action="transmittal.attachment.upload",
            entity_type="transmittal",
            entity_id=transmittal.id,
            actor=user,
            project_id=project.id,
            after={"attachment_id": str(attachment_id), "line_id": str(line.id), "kind": kind, "filename": filename},
        )
        db.commit()
        return {"attachment": _attachment_out(attachment).model_dump(mode="json")}
    except HTTPException:
        db.rollback()
        if storage_key:
            try:
                get_storage().delete(storage_key)
            except Exception:
                pass
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        if storage_key:
            try:
                get_storage().delete(storage_key)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"ذخیره پیوست ناموفق بود: {exc}") from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@router.get(
    "/transmittals/{transmittal_id}/attachments/{attachment_id}/download",
    summary="دریافت پیوست مدرک",
)
def download_attachment(
    transmittal_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
):
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_transmittal(db, user, transmittal)
    attachment = next(
        (item for item in (transmittal.meta or {}).get("attachments", []) if str(item.get("id")) == str(attachment_id)),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    _assert_native_attachment_access(db, user, transmittal, attachment)
    audit.record(
        db,
        action="transmittal.attachment.download",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=user,
        project_id=transmittal.project_id,
        after={"attachment_id": str(attachment_id)},
    )
    storage = get_storage()
    try:
        content = storage.get_bytes(attachment["storage_key"])
    except Exception as exc:  # noqa: BLE001 - hide storage implementation details
        raise HTTPException(status_code=404, detail="فایل پیوست در انبار اسناد قابل دریافت نیست.") from exc

    filename = os.path.basename(attachment.get("filename") or "attachment")
    detected_type = mimetypes.guess_type(filename)[0]
    content_type = attachment.get("content_type") or detected_type or "application/octet-stream"
    if content_type == "application/octet-stream" and detected_type:
        content_type = detected_type
    db.commit()
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/transmittals/{transmittal_id}/attachments/{attachment_id}/preview",
    summary="نمایش درون‌برنامه‌ای پیوست مدرک",
)
def preview_attachment(
    transmittal_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
):
    """Return attachment bytes with an inline disposition for authenticated preview.

    The normal download endpoint returns the authenticated bytes with an
    ``attachment`` disposition, while PDF.js and media/text previews need the
    opposite ``inline`` behaviour. Both same-origin responses avoid requiring
    browser CORS access to a public S3/MinIO endpoint.
    """
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_transmittal(db, user, transmittal)
    attachment = next(
        (item for item in (transmittal.meta or {}).get("attachments", []) if str(item.get("id")) == str(attachment_id)),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    _assert_native_attachment_access(db, user, transmittal, attachment)

    storage = get_storage()
    storage_key = attachment.get("storage_key")
    if not storage_key or not storage.exists(storage_key):
        raise HTTPException(status_code=404, detail="فایل پیوست در انبار اسناد پیدا نشد.")
    try:
        content = storage.get_bytes(storage_key)
    except Exception as exc:  # noqa: BLE001 - hide storage implementation details
        raise HTTPException(status_code=404, detail="فایل پیوست در انبار اسناد قابل دریافت نیست.") from exc

    filename = os.path.basename(attachment.get("filename") or "attachment")
    detected_type = mimetypes.guess_type(filename)[0]
    content_type = attachment.get("content_type") or detected_type or "application/octet-stream"
    if content_type == "application/octet-stream" and detected_type:
        content_type = detected_type
    audit.record(
        db,
        action="transmittal.attachment.preview",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=user,
        project_id=transmittal.project_id,
        after={"attachment_id": str(attachment_id), "filename": filename},
    )
    db.commit()
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "private, no-store",
        },
    )


@router.patch(
    "/transmittals/{transmittal_id}/attachments/{attachment_id}",
    response_model=AttachmentOut,
    summary="ویرایش نام یا جایگزینی پیوست مدرک",
)
async def update_attachment(
    transmittal_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    filename: str = Form(...),
    kind: str = Form(..., description="pdf یا native"),
    file: UploadFile | None = File(None),
) -> AttachmentOut:
    """Rename an attachment or replace its bytes without losing its audit identity."""
    if kind not in {"pdf", "native"}:
        raise HTTPException(status_code=422, detail="نوع پیوست باید pdf یا native باشد.")
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    project = acl.require_transmittal(db, user, transmittal)
    _assert_attachments_editable(transmittal)
    if not _can_manage_attachments(db, user, transmittal):
        raise acl.AccessDenied("فقط مسئول کنترل یا ایجادکننده مجاز به ویرایش پیوست است.")
    attachments = list((transmittal.meta or {}).get("attachments") or [])
    attachment = next((item for item in attachments if str(item.get("id")) == str(attachment_id)), None)
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    if attachment.get("kind") == "native" and not _is_transmittal_issuer(user, transmittal):
        raise acl.AccessDenied("ویرایش فایل Native فقط توسط صادرکننده ترانسمیتال مجاز است.")

    safe_filename = os.path.basename(filename.strip())[:500]
    if not safe_filename:
        raise HTTPException(status_code=422, detail="نام فایل پیوست نمی‌تواند خالی باشد.")
    if file is None and kind != attachment.get("kind"):
        raise HTTPException(status_code=422, detail="برای تغییر نوع پیوست، فایل جدید همان نوع را انتخاب کنید.")
    old_storage_key = attachment.get("storage_key")
    temp_path = ""
    new_storage_key = old_storage_key
    new_size = int(attachment.get("size_bytes") or 0)
    new_sha256 = attachment.get("sha256")
    new_content_type = attachment.get("content_type") or "application/octet-stream"
    try:
        if file is not None:
            max_bytes = settings.max_upload_mb * 1024 * 1024
            digest = hashlib.sha256()
            size = 0
            head = b""
            suffix = os.path.splitext(safe_filename)[1] or ".bin"
            with tempfile.NamedTemporaryFile(prefix="edms-attachment-replace-", suffix=suffix, delete=False) as temp:
                temp_path = temp.name
                while chunk := await file.read(1024 * 1024):
                    if not head:
                        head = chunk[:8]
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(
                            status_code=422,
                            detail=f"حجم فایل بیشتر از حد مجاز ({settings.max_upload_mb} مگابایت) است.",
                        )
                    digest.update(chunk)
                    temp.write(chunk)
            if size == 0:
                raise HTTPException(status_code=422, detail="فایل پیوست خالی است.")
            if kind == "pdf" and not head.startswith(b"%PDF-"):
                raise HTTPException(status_code=422, detail="پیوست PDF معتبر نیست.")
            new_storage_key = build_key(
                project.code,
                "transmittals",
                str(transmittal.id),
                "attachments",
                f"{attachment_id}-{uuid.uuid4()}{suffix.lower()}",
            )
            detected_type = mimetypes.guess_type(safe_filename)[0]
            new_content_type = file.content_type or detected_type or "application/octet-stream"
            if new_content_type == "application/octet-stream" and detected_type:
                new_content_type = detected_type
            get_storage().put_file(new_storage_key, temp_path, content_type=new_content_type)
            new_size = size
            new_sha256 = digest.hexdigest()

        existing_access_ids = _attachment_access_ids(attachment)
        updated = {
            **attachment,
            "filename": safe_filename,
            "kind": kind,
            "content_type": new_content_type,
            "size_bytes": new_size,
            "sha256": new_sha256,
            "storage_key": new_storage_key,
            "uploaded_by_name": attachment.get("uploaded_by_name") or user.full_name,
            "access_user_ids": (
                sorted(existing_access_ids or {str(attachment.get("uploaded_by_id") or user.id)})
                if kind == "native"
                else []
            ),
        }
        meta = dict(transmittal.meta or {})
        meta["attachments"] = [updated if str(item.get("id")) == str(attachment_id) else item for item in attachments]
        transmittal.meta = meta
        audit.record(
            db,
            action="transmittal.attachment.update",
            entity_type="transmittal",
            entity_id=transmittal.id,
            actor=user,
            project_id=project.id,
            before={"attachment_id": str(attachment_id), "filename": attachment.get("filename"), "kind": attachment.get("kind")},
            after={"attachment_id": str(attachment_id), "filename": safe_filename, "kind": kind, "replaced": file is not None},
        )
        db.commit()
        if file is not None and old_storage_key and old_storage_key != new_storage_key:
            try:
                get_storage().delete(old_storage_key)
            except Exception:
                pass
        return _attachment_out(updated)
    except HTTPException:
        db.rollback()
        if new_storage_key != old_storage_key:
            try:
                get_storage().delete(new_storage_key)
            except Exception:
                pass
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        if new_storage_key != old_storage_key:
            try:
                get_storage().delete(new_storage_key)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"ویرایش پیوست ناموفق بود: {exc}") from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@router.patch(
    "/transmittals/{transmittal_id}/attachments/{attachment_id}/access",
    response_model=AttachmentOut,
    summary="تعیین سطح دسترسی فایل Native",
)
def update_attachment_access(
    transmittal_id: uuid.UUID,
    attachment_id: uuid.UUID,
    payload: AttachmentAccessUpdate,
    db: DbSession,
    user: CurrentUser,
) -> AttachmentOut:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    project = acl.require_transmittal(db, user, transmittal)
    _assert_attachments_editable(transmittal)
    if not _can_manage_attachments(db, user, transmittal) or not _is_transmittal_issuer(user, transmittal):
        raise acl.AccessDenied("فقط صادرکننده ترانسمیتال می‌تواند سطح دسترسی فایل را تعیین کند.")

    attachments = list((transmittal.meta or {}).get("attachments") or [])
    attachment = next((item for item in attachments if str(item.get("id")) == str(attachment_id)), None)
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    if attachment.get("kind") != "native":
        raise HTTPException(status_code=422, detail="سطح دسترسی جداگانه فقط برای فایل Native قابل تعیین است.")

    requested_uuid_ids = set(payload.user_ids)
    requested_ids = {str(value) for value in requested_uuid_ids}
    project_user_ids = {
        str(value)
        for value in db.execute(
            select(ProjectMember.user_id).where(
                ProjectMember.project_id == transmittal.project_id,
                *([ProjectMember.user_id.in_(list(requested_uuid_ids))] if requested_uuid_ids else []),
            )
        ).scalars()
    }
    if requested_ids and project_user_ids != requested_ids:
        raise HTTPException(status_code=422, detail="همه کاربران انتخاب‌شده باید عضو فعال همین پروژه باشند.")
    for user_id in requested_uuid_ids:
        target = db.get(User, user_id)
        if target is None or not target.is_active or not acl.can_access_transmittal(db, target, transmittal):
            raise HTTPException(status_code=422, detail="یکی از کاربران انتخاب‌شده به این ترانسمیتال دسترسی ندارد.")

    owner_ids = {
        str(value)
        for value in (
            attachment.get("uploaded_by_id"),
            transmittal.created_by_id,
            transmittal.sender_id,
            transmittal.issued_by_id,
        )
        if value
    }
    access_ids = sorted(requested_ids | owner_ids)
    updated = {**attachment, "access_user_ids": access_ids}
    meta = dict(transmittal.meta or {})
    meta["attachments"] = [updated if str(item.get("id")) == str(attachment_id) else item for item in attachments]
    transmittal.meta = meta
    audit.record(
        db,
        action="transmittal.attachment.access.update",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=user,
        project_id=project.id,
        after={"attachment_id": str(attachment_id), "user_ids": access_ids},
    )
    db.commit()
    return _attachment_out(updated)


@router.delete(
    "/transmittals/{transmittal_id}/attachments/{attachment_id}",
    response_model=TransmittalDetail,
    summary="حذف پیوست مدرک",
)
def delete_attachment(
    transmittal_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> TransmittalDetail:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_transmittal(db, user, transmittal)
    _assert_attachments_editable(transmittal)
    can_manage = acl.has_role(
        db,
        user,
        transmittal.project_id,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
        ProjectRole.SYSTEM_ADMIN,
    )
    is_manual_owner = (
        transmittal.manual_workflow
        and transmittal.created_by_id == user.id
        and str(transmittal.status) == str(TransmittalStatus.DRAFT)
    )
    if not can_manage and not is_manual_owner:
        raise acl.AccessDenied("فقط ایجادکننده پیش‌نویس یا مسئول کنترل می‌تواند پیوست را حذف کند.")
    attachments = list((transmittal.meta or {}).get("attachments") or [])
    attachment = next((item for item in attachments if str(item.get("id")) == str(attachment_id)), None)
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    if attachment.get("kind") == "native" and not _is_transmittal_issuer(user, transmittal):
        raise acl.AccessDenied("حذف فایل Native فقط توسط صادرکننده ترانسمیتال مجاز است.")
    try:
        get_storage().delete(attachment["storage_key"])
    except Exception:
        # The DB metadata is still removed; a missing object must not block cleanup.
        pass
    meta = dict(transmittal.meta or {})
    meta["attachments"] = [item for item in attachments if str(item.get("id")) != str(attachment_id)]
    transmittal.meta = meta
    audit.record(
        db,
        action="transmittal.attachment.delete",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=user,
        project_id=transmittal.project_id,
        before={"attachment_id": str(attachment_id), "filename": attachment.get("filename")},
    )
    db.commit()
    db.refresh(transmittal)
    return _detail(db, transmittal)


@router.delete(
    "/transmittals/{transmittal_id}",
    status_code=204,
    response_model=None,
    summary="حذف پیش‌نویس برگه ارسال از کارتابل",
)
def delete_transmittal(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    """Remove an unfinished inbox sheet without deleting registered documents.

    Issued or routed sheets are records in the document-control audit trail and
    therefore use the existing cancel workflow instead of physical deletion.
    """
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")

    acl.require_transmittal(db, user, transmittal)
    can_manage = acl.has_role(
        db,
        user,
        transmittal.project_id,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
        ProjectRole.SYSTEM_ADMIN,
    )
    if transmittal.created_by_id != user.id and not can_manage:
        raise HTTPException(status_code=403, detail="فقط سازنده یا مسئول کنترل برگه می‌تواند پیش‌نویس را حذف کند.")
    if str(transmittal.status) != str(TransmittalStatus.DRAFT):
        raise HTTPException(
            status_code=409,
            detail="برگه‌های صادرشده یا در گردش حذف نمی‌شوند؛ برای آن‌ها از عملیات «لغو» استفاده کنید.",
        )

    before = {
        "number": transmittal.number,
        "status": str(transmittal.status),
        "stage": str(transmittal.stage),
        "line_count": len(transmittal.lines),
        "recipient_count": len(transmittal.recipients),
    }
    audit.record(
        db,
        action="transmittal.delete",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=user,
        project_id=transmittal.project_id,
        before=before,
    )
    db.delete(transmittal)
    db.commit()
    return Response(status_code=204)


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
    acl.require_transmittal(db, user, transmittal)
    can_manage = acl.has_role(
        db,
        user,
        transmittal.project_id,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
        ProjectRole.SYSTEM_ADMIN,
    )
    if not can_manage and not (transmittal.manual_workflow and transmittal.created_by_id == user.id):
        raise acl.AccessDenied("صدور نهایی فقط برای ایجادکننده ترانسمیتال دستی یا مسئول کنترل مجاز است.")
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
    acl.require_transmittal(db, user, transmittal)
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
    acl.require_transmittal(db, user, transmittal, [ProjectRole.DCC, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.PROJECT_MANAGER])
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
    acl.require_transmittal(db, user, transmittal)
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
    acl.require_transmittal(db, user, transmittal, [ProjectRole.DCC, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.PROJECT_MANAGER])
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
