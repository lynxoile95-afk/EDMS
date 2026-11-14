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
    ReplySheet,
    Transmittal,
    TransmittalLine,
    TransmittalRecipient,
    TransmittalReferral,
    User,
)
from app.models.enums import (
    ActionRequired,
    ProjectRole,
    PurposeOfIssue,
    ReferralStatus,
    TransmittalStatus,
    TransmittalType,
)
from app.schemas.transmittals import (
    AcknowledgeRequest,
    AttachmentAccessUpdate,
    CancelRequest,
    CompleteRequest,
    FlowHistoryOut,
    ForwardRequest,
    IssueRequest,
    LineOut,
    PersonnelReferralItem,
    RecipientOut,
    ReferPersonnelRequest,
    ReferRequest,
    AttachmentOut,
    ReferralOut,
    ReplySheetCreate,
    ReplySheetNoCommentRequest,
    ReplySheetOut,
    ReplySheetReferRequest,
    ReplySheetRowOut,
    TransmittalCreate,
    TransmittalDetail,
    TransmittalOut,
)
from app.services import acl, audit, documents, notification, numbering, rendering, reply_sheet, routing, transmittal_service
from app.services.project_settings import merge_settings
from app.services.storage import build_key, get_storage
from app.utils.jalali import format_jalali

router = APIRouter(tags=["transmittals"])

_FINALIZED_ATTACHMENT_STATUSES = {
    TransmittalStatus.ISSUED.value,
    TransmittalStatus.PARTIALLY_ACKNOWLEDGED.value,
    TransmittalStatus.ACKNOWLEDGED.value,
    TransmittalStatus.CLOSED.value,
    TransmittalStatus.FINAL.value,
    TransmittalStatus.CANCELLED.value,
}

_RELATED_ATTACHMENT_TYPES = {
    "letter",
    "correspondence",
    "meeting_minutes",
    "image",
}


def _assert_attachments_editable(
    transmittal: Transmittal,
    *,
    allow_related_append: bool = False,
    allow_native_append: bool = False,
) -> None:
    """Lock ordinary attachments after issue, with a narrow Native exception.

    PDF attachments keep the existing immutable-after-issue rule. Native files
    are the issuer's source files, so the issuer may add or maintain them after
    an issued sheet has a number. The exception stops at a final or cancelled
    sheet; related records keep their existing append-only behavior.
    """
    status_value = getattr(transmittal.status, "value", transmittal.status)
    status_text = str(status_value)
    if status_text == TransmittalStatus.CANCELLED.value:
        raise HTTPException(
            status_code=409,
            detail="پس از ابطال ترانسمیتال، افزودن، ویرایش یا حذف فایل پیوست مجاز نیست.",
        )
    is_final = status_text in {TransmittalStatus.FINAL.value, TransmittalStatus.CLOSED.value} or bool(
        (transmittal.meta or {}).get("final")
    )
    if allow_native_append and is_final:
        raise HTTPException(
            status_code=409,
            detail="پس از نهایی‌شدن ترانسمیتال، فایل Native دیگر قابل ویرایش نیست.",
        )
    if not allow_related_append and not allow_native_append and (
        str(status_value) in _FINALIZED_ATTACHMENT_STATUSES or transmittal.number
    ):
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
        sender_id=transmittal.sender_id,
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


def _flow_history_out(db, history) -> list[FlowHistoryOut]:
    actor_ids = {entry.actor_id for entry in history if entry.actor_id}
    actor_names = {
        actor.id: actor.full_name
        for actor in db.execute(select(User).where(User.id.in_(actor_ids))).scalars()
    } if actor_ids else {}
    return [
        FlowHistoryOut(
            id=entry.id,
            action=entry.action,
            status=(entry.after or {}).get("status") or (entry.after or {}).get("transmittal_status"),
            actor_id=entry.actor_id,
            actor_name=actor_names.get(entry.actor_id) if entry.actor_id else None,
            actor_email=entry.actor_email,
            created_at=entry.created_at,
            before=entry.before or {},
            after=entry.after or {},
        )
        for entry in history
    ]


def _reply_sheet_out(db, sheet: ReplySheet | None) -> ReplySheetOut | None:
    if sheet is None:
        return None
    history = reply_sheet.flow_history(db, sheet.transmittal_id, sheet.id)
    history_out = _flow_history_out(db, history)
    referral_event = next(
        (entry for entry in reversed(history) if entry.action == "reply_sheet.refer"),
        None,
    )
    referral_after = referral_event.after or {} if referral_event is not None else {}
    rows = [
        ReplySheetRowOut(
            **row.as_dict(),
            view_url=f"/transmittals/{sheet.transmittal_id}/reply-sheet/{row.id}" if row.id else None,
        )
        for row in reply_sheet.reply_sheet_rows(db, sheet.transmittal)
        if row.reply_sheet_id == sheet.id
    ]
    return ReplySheetOut(
        id=sheet.id,
        transmittal_id=sheet.transmittal_id,
        number=sheet.number,
        sequence_no=sheet.sequence_no,
        status=sheet.status,
        replied_by_id=sheet.replied_by_id,
        replied_by_name=sheet.replied_by.full_name if sheet.replied_by else None,
        created_by_id=sheet.created_by_id,
        created_by_name=sheet.created_by.full_name if sheet.created_by else None,
        assigned_to_id=sheet.assigned_to_id,
        assigned_to_name=sheet.assigned_to.full_name if sheet.assigned_to else None,
        created_at=sheet.created_at,
        completed_at=sheet.completed_at,
        judgment=sheet.judgment,
        doc_status=sheet.doc_status,
        doc_percent=sheet.doc_percent,
        no_comment=sheet.no_comment,
        remarks=sheet.remarks,
        referred_to_id=referral_after.get("to_user_id"),
        referred_to_name=referral_after.get("to_user_name"),
        referred_at=referral_event.created_at if referral_event is not None else None,
        referral_note=referral_after.get("note"),
        referral_status="referred" if referral_event is not None else None,
        flow_history=history_out,
        rows=rows,
    )


def _attachment_out(raw: dict) -> AttachmentOut:
    access_user_ids = [str(value) for value in (raw.get("access_user_ids") or [])]
    return AttachmentOut(
        id=raw["id"],
        line_id=raw.get("line_id"),
        revision_id=raw.get("revision_id"),
        kind=raw["kind"],
        related_type=raw.get("related_type"),
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


def _is_afc_transmittal(transmittal: Transmittal) -> bool:
    purpose = str(getattr(transmittal.purpose, "value", transmittal.purpose)).upper()
    return purpose == "AFC" or any(
        line.revision is not None
        and line.revision.mdl_item is not None
        and str(line.revision.mdl_item.poi or "").upper() == "AFC"
        for line in transmittal.lines
    )


def _existing_os_for_afc(db, afc: Transmittal) -> Transmittal | None:
    """Find the OS draft created from one finalized AFC.

    The OS line points at the newly-created B-family revision, not at the AFC
    revision that was on the source line.  Looking up by source metadata keeps
    the action idempotent after the AFC revision is superseded, so refreshing or
    double-clicking the toolbar cannot create B1, B2, ... duplicates.
    """
    source_id = str(afc.id)
    candidates = db.execute(
        select(Transmittal)
        .where(
            Transmittal.project_id == afc.project_id,
            Transmittal.type == TransmittalType.FINAL_OS,
        )
        .order_by(Transmittal.created_at.desc())
    ).scalars()
    for candidate in candidates:
        meta = candidate.meta or {}
        if str(meta.get("source_afc_transmittal_id") or "") == source_id:
            return candidate
    return None


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
            doc_percent=line.doc_percent,
            poi=(line.revision.mdl_item.poi if line.revision and line.revision.mdl_item else None),
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
    current_sheet = reply_sheet.latest_reply_sheet(db, transmittal.id)
    purpose_value = str(getattr(transmittal.purpose, "value", transmittal.purpose))
    is_afc = purpose_value.upper() == "AFC" or any(
        line.revision is not None and line.revision.mdl_item is not None
        and str(line.revision.mdl_item.poi or "").upper() == "AFC"
        for line in transmittal.lines
    )
    is_final = str(getattr(transmittal.status, "value", transmittal.status)) == TransmittalStatus.FINAL.value or bool(
        (transmittal.meta or {}).get("final")
    )
    os_transmittal_id = None
    if is_final and is_afc:
        existing_os = _existing_os_for_afc(db, transmittal)
        os_transmittal_id = existing_os.id if existing_os is not None else None
    return TransmittalDetail(
        **base.model_dump(),
        lines=lines,
        recipients=recipients,
        referrals=[_referral_out(item) for item in transmittal.referrals],
        attachments=[_attachment_out(item) for item in (transmittal.meta or {}).get("attachments", [])],
        pdf_url=pdf_url,
        preview_number=None if transmittal.number else preview,
        stage_label=routing.STAGE_LABELS.get(str(transmittal.stage)),
        is_final=is_final,
        reply_sheet=_reply_sheet_out(db, current_sheet),
        flow_history=_flow_history_out(db, reply_sheet.transmittal_flow_history(db, transmittal.id)),
        os_available=is_final and is_afc and os_transmittal_id is None,
        os_transmittal_id=os_transmittal_id,
    )


def _transmittal_for_reply(db, user: User, transmittal_id: uuid.UUID) -> Transmittal:
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    acl.require_transmittal(db, user, transmittal)
    return transmittal


@router.get(
    "/transmittals/{transmittal_id}/reply-sheet",
    response_model=list[ReplySheetRowOut],
    summary="فهرست ردیف‌های Reply Sheet",
)
def get_reply_sheet_rows(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[ReplySheetRowOut]:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    return [
        ReplySheetRowOut(
            **row.as_dict(),
            view_url=f"/transmittals/{transmittal.id}/reply-sheet/{row.id}" if row.id else None,
        )
        for row in reply_sheet.reply_sheet_rows(db, transmittal)
    ]


@router.get(
    "/transmittals/{transmittal_id}/reply-sheet.pdf",
    summary="دریافت PDF برگه پاسخ",
)
def download_reply_sheet_pdf(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    sheet = reply_sheet.latest_reply_sheet(db, transmittal.id)
    rows = reply_sheet.reply_sheet_rows(db, transmittal)
    project = db.get(Project, transmittal.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="پروژه یافت نشد.")
    context = rendering.build_reply_context(
        number=sheet.number if sheet is not None else f"{transmittal.number or 'DRAFT'}-R01",
        project_name=project.name,
        from_org=transmittal.from_org or project.contractor_name or "",
        to_org=transmittal.to_org or project.client_name or "",
        transmittal_number=transmittal.number or "DRAFT",
        issue_date=transmittal.issue_date or transmittal.issued_at,
        created_at=sheet.created_at if sheet is not None else transmittal.created_at,
        replied_by=sheet.replied_by.full_name if sheet is not None and sheet.replied_by else user.full_name,
        status=sheet.status if sheet is not None else str(transmittal.status),
        doc_percent=sheet.doc_percent if sheet is not None else 0,
        rows=[row.as_dict() for row in rows],
        transmittal_id=str(transmittal.id),
    )
    payload = rendering.render_reply_sheet(context)
    filename = f"{sheet.number if sheet is not None else transmittal.number or 'transmittal'}-reply-sheet.pdf"
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/transmittals/{transmittal_id}/reply-sheet/record",
    response_model=ReplySheetOut | None,
    summary="مشاهده رکورد و Flow History آخرین Reply Sheet",
)
def get_reply_sheet_record(transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ReplySheetOut | None:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    return _reply_sheet_out(db, reply_sheet.latest_reply_sheet(db, transmittal.id))


@router.post(
    "/transmittals/{transmittal_id}/reply-sheet",
    response_model=ReplySheetOut,
    status_code=201,
    summary="ایجاد و تکمیل Reply Sheet",
)
def create_reply_sheet(
    transmittal_id: uuid.UUID,
    payload: ReplySheetCreate,
    db: DbSession,
    user: CurrentUser,
) -> ReplySheetOut:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    try:
        sheet = reply_sheet.create_reply_sheet(db, transmittal=transmittal, actor=user, payload=payload)
        db.commit()
        db.refresh(sheet)
    except HTTPException:
        db.rollback()
        raise
    return _reply_sheet_out(db, sheet)  # type: ignore[return-value]


@router.post(
    "/transmittals/{transmittal_id}/reply-sheet/no-comment",
    response_model=ReplySheetOut,
    status_code=201,
    summary="ثبت Reply Sheet بدون نظر",
)
def create_no_comment_reply_sheet(
    transmittal_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    payload: ReplySheetNoCommentRequest | None = None,
) -> ReplySheetOut:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    try:
        sheet = reply_sheet.create_reply_sheet(
            db,
            transmittal=transmittal,
            actor=user,
            no_comment_payload=payload or ReplySheetNoCommentRequest(),
        )
        db.commit()
        db.refresh(sheet)
    except HTTPException:
        db.rollback()
        raise
    return _reply_sheet_out(db, sheet)  # type: ignore[return-value]


@router.post(
    "/transmittals/{transmittal_id}/reply-sheet/refer",
    response_model=ReplySheetOut,
    summary="ارجاع Reply Sheet به تهیه‌کننده نقشه",
)
@router.post(
    "/transmittals/{transmittal_id}/reply-sheet/refer-to-preparer",
    response_model=ReplySheetOut,
    include_in_schema=False,
)
def refer_reply_sheet(
    transmittal_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    payload: ReplySheetReferRequest | None = None,
) -> ReplySheetOut:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    try:
        sheet = reply_sheet.refer_reply_sheet(
            db,
            transmittal=transmittal,
            actor=user,
            payload=payload,
        )
        db.commit()
        db.refresh(sheet)
    except HTTPException:
        db.rollback()
        raise
    return _reply_sheet_out(db, sheet)  # type: ignore[return-value]


@router.post(
    "/transmittals/{transmittal_id}/reply-sheet/no-comment-finalize",
    response_model=TransmittalDetail,
    summary="No Comment و نهایی‌سازی AFC توسط Project Manager",
)
@router.post(
    "/transmittals/{transmittal_id}/no-comment-finalize",
    response_model=TransmittalDetail,
    include_in_schema=False,
)
def no_comment_finalize(
    transmittal_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    payload: ReplySheetNoCommentRequest | None = None,
) -> TransmittalDetail:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    body = payload or ReplySheetNoCommentRequest()
    try:
        latest = reply_sheet.latest_reply_sheet(db, transmittal.id)
        if latest is None or not latest.no_comment:
            reply_sheet.create_reply_sheet(
                db,
                transmittal=transmittal,
                actor=user,
                no_comment_payload=body,
            )
        # A combined action is useful from the reference toolbar.  If a normal
        # draft is being finalized, issue it first so the final record has a
        # permanent transmittal number and an immutable PDF.
        if transmittal.status in {TransmittalStatus.DRAFT, TransmittalStatus.PENDING_APPROVAL}:
            transmittal_service.issue_transmittal(
                db, transmittal=transmittal, actor=user, storage=get_storage()
            )
            routing.mark_issued(db, transmittal)
        reply_sheet.mark_afc_no_comment_final(db, transmittal=transmittal, actor=user)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=f"نهایی‌سازی AFC ناموفق بود: {exc}") from exc
    db.refresh(transmittal)
    return _detail(db, transmittal)


@router.post(
    "/transmittals/{transmittal_id}/reply-sheet/create-os",
    response_model=TransmittalDetail,
    summary="ایجاد پیش‌نویس OS از مدرک AFC نهایی",
)
@router.post(
    "/transmittals/{transmittal_id}/create-os",
    response_model=TransmittalDetail,
    include_in_schema=False,
)
def create_os_from_final_afc(
    transmittal_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> TransmittalDetail:
    transmittal = _transmittal_for_reply(db, user, transmittal_id)
    reply_sheet.assert_project_manager(db, user, transmittal.project_id)
    purpose_value = str(getattr(transmittal.purpose, "value", transmittal.purpose)).upper()
    is_afc = purpose_value == "AFC" or any(
        line.revision is not None and line.revision.mdl_item is not None
        and str(line.revision.mdl_item.poi or "").upper() == "AFC"
        for line in transmittal.lines
    )
    if not is_afc or str(getattr(transmittal.status, "value", transmittal.status)) != TransmittalStatus.FINAL.value:
        raise HTTPException(status_code=409, detail="ابتدا باید ترانسمیتال AFC با No Comment نهایی شده باشد.")

    existing_os = _existing_os_for_afc(db, transmittal)
    if existing_os is not None:
        return _detail(db, existing_os)

    try:
        os_revisions = []
        for line in transmittal.lines:
            if line.revision is None or line.revision.mdl_item is None:
                continue
            revision = documents.register_revision(
                db,
                project=db.get(Project, transmittal.project_id),
                mdl_item=line.revision.mdl_item,
                rev=reply_sheet.next_os_revision(db, line.revision.mdl_item.id),
                file_hash=hashlib.sha256(
                    f"os|{transmittal.id}|{line.revision.mdl_item.id}|{uuid.uuid4()}".encode()
                ).hexdigest(),
                title=line.revision.mdl_item.title,
                page_count=line.revision.page_count,
                actor=user,
                notes="پس از نهایی‌سازی AFC ایجاد شد",
                transmittal_type=TransmittalType.FINAL_OS,
            )
            os_revisions.append(revision)
        if not os_revisions:
            raise HTTPException(status_code=422, detail="مدرک فعالی برای ایجاد OS وجود ندارد.")
        project = db.get(Project, transmittal.project_id)
        drafts = transmittal_service.create_draft_transmittals(
            db,
            project=project,
            revisions=os_revisions,
            purpose=PurposeOfIssue.AFC,
            transmittal_type=TransmittalType.FINAL_OS,
            actor=user,
            remarks="ایجاد از AFC نهایی",
            settings_override={"grouping_strategy": "one_per_session"},
        )
        if not drafts:
            raise HTTPException(status_code=422, detail="پیش‌نویس OS ایجاد نشد.")
        os_draft = drafts[0]
        os_draft.manual_workflow = True
        os_draft.final_approver_id = user.id
        os_draft.meta = {
            **(os_draft.meta or {}),
            "creation_mode": "from_final_afc",
            "source_afc_transmittal_id": str(transmittal.id),
            "revision_family": "B",
        }
        for line in os_draft.lines:
            line.doc_status = "Draft"
            line.doc_percent = 0
        audit.record(
            db,
            action="transmittal.os.create_from_afc",
            entity_type="transmittal",
            entity_id=os_draft.id,
            actor=user,
            project_id=transmittal.project_id,
            after={"source_afc_transmittal_id": str(transmittal.id), "revision": [r.rev for r in os_revisions]},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=422, detail=f"ایجاد OS ناموفق بود: {exc}") from exc
    db.refresh(os_draft)
    return _detail(db, os_draft)


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
    # Native files are issuer-owned source files. The exception only bypasses
    # the ordinary post-issue attachment lock; the issuer check below remains
    # mandatory and cancellation/finalization are still blocked by the guard.
    _assert_attachments_editable(transmittal, allow_native_append=kind == "native")
    # Native files belong to the issuer, not merely to a document-control role.
    # PDF attachments keep the existing DCC/project-control permission model.
    if kind == "native":
        if not _is_transmittal_issuer(user, transmittal):
            raise acl.AccessDenied("بارگذاری فایل Native فقط توسط صادرکننده ترانسمیتال مجاز است.")
    elif not _can_manage_attachments(db, user, transmittal):
        raise acl.AccessDenied("فقط مسئول کنترل یا ایجادکننده مجاز به بارگذاری پیوست است.")
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


@router.post(
    "/transmittals/{transmittal_id}/related-attachments",
    status_code=201,
    summary="پیوست نامه، مکاتبه، صورتجلسه یا تصویر به ترانسمیتال",
)
async def upload_related_attachment(
    transmittal_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    related_type: str = Form(..., description="letter، correspondence، meeting_minutes یا image"),
    file: UploadFile = File(...),
) -> dict:
    """Attach a transmittal-level supporting record from the Letters tab.

    Unlike PDF and Native files, these files do not belong to one document line:
    a request letter, meeting minutes or site photograph can describe the whole
    transmittal. They therefore use ``kind=related`` and remain append-only after
    issue while respecting the existing transmittal ACL.
    """
    related_type = related_type.strip().lower()
    if related_type not in _RELATED_ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail="نوع پیوست باید نامه، مکاتبه، صورتجلسه یا تصویر باشد.",
        )
    transmittal = db.get(Transmittal, transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    project = acl.require_transmittal(db, user, transmittal)
    _assert_attachments_editable(transmittal, allow_related_append=True)
    can_manage_related = acl.has_role(
        db,
        user,
        transmittal.project_id,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
        ProjectRole.SYSTEM_ADMIN,
    ) or _is_transmittal_issuer(user, transmittal)
    if not can_manage_related:
        raise acl.AccessDenied("فقط مسئول کنترل یا صادرکننده ترانسمیتال مجاز به بارگذاری فایل مرتبط است.")

    filename = os.path.basename((file.filename or "attachment").strip())[:500] or "attachment"
    max_bytes = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    suffix = os.path.splitext(filename)[1] or ".bin"
    temp_path = ""
    storage_key = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="edms-related-", suffix=suffix, delete=False) as temp:
            temp_path = temp.name
            while chunk := await file.read(1024 * 1024):
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

        attachment_id = uuid.uuid4()
        storage_key = build_key(
            project.code,
            "transmittals",
            str(transmittal.id),
            "related",
            f"{attachment_id}{suffix.lower()}",
        )
        detected_type = mimetypes.guess_type(filename)[0]
        content_type = file.content_type or detected_type or "application/octet-stream"
        if content_type == "application/octet-stream" and detected_type:
            content_type = detected_type
        get_storage().put_file(storage_key, temp_path, content_type=content_type)
        attachment = {
            "id": str(attachment_id),
            "line_id": None,
            "revision_id": None,
            "kind": "related",
            "related_type": related_type,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "storage_key": storage_key,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "uploaded_by_id": str(user.id),
            "uploaded_by_name": user.full_name,
            "access_user_ids": [],
        }
        meta = dict(transmittal.meta or {})
        meta["attachments"] = [*(meta.get("attachments") or []), attachment]
        transmittal.meta = meta
        audit.record(
            db,
            action="transmittal.related_attachment.upload",
            entity_type="transmittal",
            entity_id=transmittal.id,
            actor=user,
            project_id=transmittal.project_id,
            after={
                "attachment_id": str(attachment_id),
                "related_type": related_type,
                "filename": filename,
            },
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
        raise HTTPException(status_code=500, detail=f"ذخیره فایل مرتبط ناموفق بود: {exc}") from exc
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
    attachments = list((transmittal.meta or {}).get("attachments") or [])
    attachment = next((item for item in attachments if str(item.get("id")) == str(attachment_id)), None)
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    _assert_attachments_editable(
        transmittal,
        allow_native_append=attachment.get("kind") == "native" and kind == "native",
    )
    if attachment.get("kind") == "native":
        if not _is_transmittal_issuer(user, transmittal):
            raise acl.AccessDenied("ویرایش فایل Native فقط توسط صادرکننده ترانسمیتال مجاز است.")
    elif not _can_manage_attachments(db, user, transmittal):
        raise acl.AccessDenied("فقط مسئول کنترل یا ایجادکننده مجاز به ویرایش پیوست است.")

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
    attachments = list((transmittal.meta or {}).get("attachments") or [])
    attachment = next((item for item in attachments if str(item.get("id")) == str(attachment_id)), None)
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    if attachment.get("kind") != "native":
        raise HTTPException(status_code=422, detail="سطح دسترسی جداگانه فقط برای فایل Native قابل تعیین است.")
    _assert_attachments_editable(transmittal, allow_native_append=True)
    if not _is_transmittal_issuer(user, transmittal):
        raise acl.AccessDenied("فقط صادرکننده ترانسمیتال می‌تواند سطح دسترسی فایل را تعیین کند.")

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
    attachments = list((transmittal.meta or {}).get("attachments") or [])
    attachment = next((item for item in attachments if str(item.get("id")) == str(attachment_id)), None)
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    _assert_attachments_editable(
        transmittal,
        allow_native_append=attachment.get("kind") == "native",
    )
    if attachment.get("kind") == "native":
        if not _is_transmittal_issuer(user, transmittal):
            raise acl.AccessDenied("حذف فایل Native فقط توسط صادرکننده ترانسمیتال مجاز است.")
    elif not can_manage and not is_manual_owner:
        raise acl.AccessDenied("فقط ایجادکننده پیش‌نویس یا مسئول کنترل می‌تواند پیوست را حذف کند.")
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
    if _is_afc_transmittal(transmittal):
        reply_sheet.assert_project_manager(db, user, transmittal.project_id)
    elif not can_manage and not (transmittal.manual_workflow and transmittal.created_by_id == user.id):
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
