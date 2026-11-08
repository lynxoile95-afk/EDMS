"""Manual transmittal registration without AI extraction.

The manual path deliberately uses the same document register, revision lifecycle,
DDM resolution and transmittal renderer as the AI path.  The only difference is
that the operator supplies the MDL rows and revision values instead of accepting
metadata extracted from a PDF.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.api.routes.transmittals import _detail
from app.models import DocumentRevision, MDLItem, ProjectMember, TransmittalRecipient
from app.models.enums import ActionRequired, ProjectRole, PurposeOfIssue, TransmittalType
from app.schemas.transmittals import TransmittalDetail
from app.services import acl, transmittal_service
from app.services.documents import register_revision
from app.services.normalization import normalize_rev

router = APIRouter(tags=["transmittals"])


class ManualTransmittalLine(BaseModel):
    mdl_item_id: uuid.UUID
    revision: str = Field(default="A", min_length=1, max_length=16)
    page_count: int = Field(default=1, ge=1, le=10000)
    note: str | None = Field(default=None, max_length=2000)


class ManualTransmittalCreate(BaseModel):
    rows: list[ManualTransmittalLine] = Field(min_length=1)
    purpose: str = Field(default="IFR", pattern=r"^(IFR|IFA|IFC|IFI|IFE|IFP|IPC|RTC|ASB)$")
    type: str = Field(default="internal", pattern=r"^(internal|external)$")
    from_org: str | None = Field(default=None, max_length=255)
    to_org: str | None = Field(default=None, max_length=255)
    remarks: str | None = Field(default=None, max_length=4000)
    access_circle_id: uuid.UUID | None = None
    extra_recipient_ids: list[uuid.UUID] = Field(default_factory=list)


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
    """Create manual revisions when necessary, then create one DDM-aware draft.

    A manual revision has no source PDF by design.  It remains an append-only
    revision record and can later be superseded by a PDF-backed revision through
    the normal AI or registration workflow.
    """
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_CONTROL, ProjectRole.PMO, ProjectRole.PROJECT_MANAGER])
    acl.validate_circle_for_transmittal(db, user, project.id, payload.access_circle_id)
    if payload.type == TransmittalType.EXTERNAL.value and not acl.has_external_access(db, user, project.id):
        raise acl.AccessDenied("صدور ترانسمیتال خارجی فقط برای عضو مجاز به دسترسی خارج از حلقه امکان‌پذیر است.")
    acl.assert_recipients_allowed(
        db, user, project.id, payload.extra_recipient_ids, payload.access_circle_id
    )

    item_ids = [row.mdl_item_id for row in payload.rows]
    if len(item_ids) != len(set(item_ids)):
        raise HTTPException(status_code=422, detail="یک مدرک در ترانسمیتال دستی بیش از یک‌بار انتخاب شده است.")

    items = list(
        db.execute(
            select(MDLItem).where(MDLItem.project_id == project.id, MDLItem.id.in_(item_ids))
        ).scalars()
    )
    items_by_id = {item.id: item for item in items}
    if len(items_by_id) != len(item_ids):
        raise HTTPException(status_code=422, detail="یکی از مدارک انتخاب‌شده متعلق به این پروژه نیست.")

    if payload.extra_recipient_ids:
        member_ids = set(
            db.execute(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_id.in_(payload.extra_recipient_ids),
                )
            ).scalars()
        )
        if member_ids != set(payload.extra_recipient_ids):
            raise HTTPException(status_code=422, detail="همه گیرندگان باید عضو همین پروژه باشند.")

    revisions: list[DocumentRevision] = []
    try:
        for row in payload.rows:
            item = items_by_id[row.mdl_item_id]
            revision_token = normalize_rev(row.revision)
            if not revision_token:
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

            if current is not None and current.rev == revision_token:
                revision = current
            else:
                file_hash = hashlib.sha256(
                    f"manual-transmittal|{project.id}|{item.id}|{revision_token}".encode("utf-8")
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
                    # MDL may already carry a current revision text even when no
                    # revision record exists yet; the first manual registration is
                    # allowed to materialise that value as a revision row.
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
            # The manual screen has one explicit "create" action. Keep all
            # selected rows in that one sheet; DDM still determines actions and
            # recipients inside the group.
            settings_override={"grouping_strategy": "one_per_session"},
        )
        if not drafts:
            raise HTTPException(status_code=422, detail="ترانسمیتال دستی ایجاد نشد.")

        # DDM supplies the normal recipients. The manually selected recipients
        # supplement it, which is important for a vendor, MC or client observer
        # not represented by a DDM rule yet.
        for draft in drafts:
            if payload.from_org is not None:
                draft.from_org = payload.from_org.strip() or None
            if payload.to_org is not None:
                draft.to_org = payload.to_org.strip() or None
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
            draft.meta = {
                **(draft.meta or {}),
                "creation_mode": "manual",
                "manual_revision_count": len(revisions),
            }
            acl.assert_recipients_allowed(
                db,
                user,
                project.id,
                [recipient.user_id for recipient in draft.recipients] + payload.extra_recipient_ids,
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
