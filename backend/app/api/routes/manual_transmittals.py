"""Manual transmittal registration without AI extraction.

The manual route uses the authoritative MDL rows and creates a real draft.  The
creator can then review that draft from the personal inbox, issue it, and refer
it to one or more active project members.  Legacy access-circle and explicit
recipient fields are still accepted for older API clients, but the current UI
no longer presents those controls.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.api.routes.transmittals import _detail
from app.models import DocumentRevision, MDLItem, ProjectMember, TransmittalRecipient, User
from app.models.enums import ActionRequired, ProjectRole, PurposeOfIssue, TransmittalType
from app.schemas.transmittals import MANUAL_DOC_STATUS_PATTERN, TransmittalDetail
from app.services import acl, transmittal_service
from app.services.documents import register_revision
from app.services.normalization import normalize_rev
from app.utils.jalali import parse_date

router = APIRouter(tags=["transmittals"])


class ManualTransmittalLine(BaseModel):
    mdl_item_id: uuid.UUID
    revision: str = Field(default="A", min_length=1, max_length=16)
    page_count: int = Field(default=1, ge=1, le=10000)
    note: str | None = Field(default=None, max_length=2000)
    doc_status: str = Field(default="No Comment", pattern=MANUAL_DOC_STATUS_PATTERN)


class ManualTransmittalCreate(BaseModel):
    rows: list[ManualTransmittalLine] = Field(min_length=1)
    purpose: str = Field(default="IFR", pattern=r"^(IFR|IFA|IFC|IFI|IFE|IFP|IPC|AFC|RTC|ASB)$")
    # OT/OS are the current manual vocabulary. The two legacy values remain
    # accepted so already-integrated clients do not break during migration.
    type: str = Field(default="internal", pattern=r"^(OT|OS|internal|external)$")
    issue_date: date = Field(default_factory=date.today)
    final_approver_id: uuid.UUID | None = None
    from_org: str | None = Field(default=None, max_length=255)
    to_org: str | None = Field(default=None, max_length=255)
    remarks: str | None = Field(default=None, max_length=4000)
    # Backward-compatible fields. The current manual screen intentionally does
    # not send these values; routing after issue uses the referral action below.
    access_circle_id: uuid.UUID | None = None
    extra_recipient_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("issue_date", mode="before")
    @classmethod
    def parse_issue_date(cls, value):
        if isinstance(value, date):
            return value
        parsed = parse_date(str(value))
        if parsed is None:
            raise ValueError("تاریخ صدور را به صورت شمسی مانند ۱۴۰۵/۰۸/۰۲ وارد کنید.")
        return parsed


def _active_project_member(
    db, project_id: uuid.UUID, user_id: uuid.UUID
) -> ProjectMember | None:
    return db.execute(
        select(ProjectMember)
        .join(User, User.id == ProjectMember.user_id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            User.is_active.is_(True),
        )
        .limit(1)
    ).scalars().first()


@router.post(
    "/projects/{project_id}/transmittals/manual",
    response_model=TransmittalDetail,
    status_code=status.HTTP_201_CREATED,
    summary="ثبت دستی ترانسمیتال بدون استخراج هوش مصنوعی",
)
def create_manual_transmittal(
    project: ProjectDep,
    payload: ManualTransmittalCreate,
    db: DbSession,
    user: CurrentUser,
) -> TransmittalDetail:
    """Create one manual draft from the selected MDL rows.

    The click on this form is deliberately a draft operation.  Number allocation,
    PDF generation and notifications only happen later through the explicit final
    issue action in the creator's inbox.
    """
    acl.require_project(
        db,
        user,
        project.id,
        [
            ProjectRole.DCC,
            ProjectRole.PROJECT_CONTROL,
            ProjectRole.PMO,
            ProjectRole.PROJECT_MANAGER,
        ],
    )

    # These checks only serve legacy clients.  The current OT/OS manual form has
    # no access-circle field and does not ask the operator to select recipients.
    acl.validate_circle_for_transmittal(db, user, project.id, payload.access_circle_id)
    if payload.type == TransmittalType.EXTERNAL.value and not acl.has_external_access(
        db, user, project.id
    ):
        raise acl.AccessDenied(
            "صدور ترانسمیتال خارجی فقط برای عضو مجاز به دسترسی خارج از حلقه امکان‌پذیر است."
        )

    if (
        payload.type in {TransmittalType.ENGINEERING_OT.value, TransmittalType.FINAL_OS.value}
        and payload.final_approver_id is None
    ):
        raise HTTPException(
            status_code=422,
            detail="برای ترانسمیتال دستی OT/OS انتخاب تأییدکننده نهایی الزامی است.",
        )
    if payload.final_approver_id is not None and _active_project_member(
        db, project.id, payload.final_approver_id
    ) is None:
        raise HTTPException(
            status_code=422,
            detail="تأییدکننده نهایی باید یکی از اعضای فعال همین پروژه باشد.",
        )

    if payload.extra_recipient_ids:
        acl.assert_recipients_allowed(
            db,
            user,
            project.id,
            payload.extra_recipient_ids,
            payload.access_circle_id,
        )

    item_ids = [row.mdl_item_id for row in payload.rows]
    if len(item_ids) != len(set(item_ids)):
        raise HTTPException(
            status_code=422,
            detail="یک مدرک در ترانسمیتال دستی بیش از یک‌بار انتخاب شده است.",
        )

    items = list(
        db.execute(
            select(MDLItem).where(
                MDLItem.project_id == project.id,
                MDLItem.id.in_(item_ids),
            )
        ).scalars()
    )
    items_by_id = {item.id: item for item in items}
    if len(items_by_id) != len(item_ids):
        raise HTTPException(
            status_code=422,
            detail="یکی از مدارک انتخاب‌شده متعلق به این پروژه نیست.",
        )

    revisions: list[DocumentRevision] = []
    try:
        for row in payload.rows:
            item = items_by_id[row.mdl_item_id]
            revision_token = normalize_rev(row.revision)
            is_ot_os = payload.type in {
                TransmittalType.ENGINEERING_OT.value,
                TransmittalType.FINAL_OS.value,
            }
            if not revision_token and not is_ot_os:
                raise HTTPException(
                    status_code=422,
                    detail=f"بازنگری مدرک «{item.doc_code}» معتبر نیست.",
                )

            current = db.execute(
                select(DocumentRevision)
                .where(
                    DocumentRevision.mdl_item_id == item.id,
                    DocumentRevision.is_current.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()

            # OT/OS revision allocation is authoritative in the data layer:
            # every new OT gets A0/A1/…, while OS starts its own B0 family.
            # The value typed by the UI is retained only for legacy internal
            # transmittals and is never allowed to rewind an OT/OS history.
            if is_ot_os:
                file_hash = hashlib.sha256(
                    f"manual-transmittal|{project.id}|{item.id}|{payload.type}|{uuid.uuid4()}".encode(
                        "utf-8"
                    )
                ).hexdigest()
                revision = register_revision(
                    db,
                    project=project,
                    mdl_item=item,
                    rev=revision_token or "A0",
                    file_hash=file_hash,
                    title=item.title,
                    page_count=row.page_count,
                    actor=user,
                    notes=row.note or "بازنگری ثبت‌شده از مسیر ترانسمیتال دستی",
                    allow_equal_revision=False,
                    transmittal_type=payload.type,
                )
            elif current is not None and current.rev == revision_token:
                revision = current
            else:
                file_hash = hashlib.sha256(
                    f"manual-transmittal|{project.id}|{item.id}|{revision_token}".encode(
                        "utf-8"
                    )
                ).hexdigest()
                revision = register_revision(
                    db,
                    project=project,
                    mdl_item=item,
                    rev=revision_token,
                    file_hash=file_hash,
                    title=item.title,
                    page_count=row.page_count,
                    actor=user,
                    notes=row.note or "بازنگری ثبت‌شده از مسیر ترانسمیتال دستی",
                    allow_equal_revision=current is None,
                )
            revisions.append(revision)

        drafts = transmittal_service.create_draft_transmittals(
            db,
            project=project,
            revisions=revisions,
            purpose=PurposeOfIssue(payload.purpose),
            transmittal_type=TransmittalType(payload.type),
            actor=user,
            remarks=payload.remarks,
            access_circle_id=payload.access_circle_id,
            settings_override={"grouping_strategy": "one_per_session"},
        )
        if not drafts:
            raise HTTPException(status_code=422, detail="ترانسمیتال دستی ایجاد نشد.")

        for draft in drafts:
            draft.manual_workflow = True
            draft.issue_date = payload.issue_date
            draft.final_approver_id = payload.final_approver_id
            draft.meta = {
                **(draft.meta or {}),
                "creation_mode": "manual",
                "manual_revision_count": len(revisions),
                "manual_doc_status_options": [
                    "Commented",
                    "No Comment",
                    "Approve As Note",
                ],
            }

            lines_by_revision = {line.revision_id: line for line in draft.lines}
            for row, revision in zip(payload.rows, revisions, strict=True):
                line = lines_by_revision.get(revision.id)
                if line is not None:
                    line.doc_status = row.doc_status

            # Legacy clients may still add explicit recipients. The current UI
            # leaves this empty and performs personnel referral after final issue.
            existing_recipient_ids = {recipient.user_id for recipient in draft.recipients}
            for recipient_id in payload.extra_recipient_ids:
                if recipient_id in existing_recipient_ids:
                    continue
                db.add(
                    TransmittalRecipient(
                        transmittal_id=draft.id,
                        user_id=recipient_id,
                        action=ActionRequired.INFO,
                    )
                )
                existing_recipient_ids.add(recipient_id)

            acl.assert_recipients_allowed(
                db,
                user,
                project.id,
                [recipient.user_id for recipient in draft.recipients]
                + payload.extra_recipient_ids,
                payload.access_circle_id,
            )

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    db.refresh(drafts[0])
    return _detail(db, drafts[0])
