"""Registered documents, revisions and their history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import AuditLog, DocumentRevision, ExtractionResult, MDLItem, Transmittal, TransmittalLine
from app.schemas.transmittals import DocumentRecordOut, LineOut
from app.services import acl, documents as document_service
from app.utils.jalali import format_jalali

router = APIRouter(tags=["documents"])


@router.get("/revisions/{revision_id}", summary="جزئیات یک بازنگری")
def get_revision(revision_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    acl.require_project(db, user, revision.project_id)
    item = revision.mdl_item
    return {
        "id": str(revision.id),
        "rev": revision.rev,
        "status": str(revision.status),
        "is_current": revision.is_current,
        "page_count": revision.page_count,
        "registered_at": revision.registered_at.isoformat() if revision.registered_at else None,
        "registered_at_jalali": format_jalali(revision.registered_at),
        "file_hash": revision.file_hash,
        "notes": revision.notes,
        "doc": {
            "id": str(item.id) if item else None,
            "doc_code": item.doc_code if item else None,
            "title": item.title if item else None,
            "discipline": item.discipline.code if item and item.discipline else None,
            "unit": item.unit.code if item and item.unit else None,
            "doc_type": item.doc_type if item else None,
            "doc_status": item.status if item else None,
        }
        if item
        else None,
        "download_url": f"/api/v1/files/revisions/{revision.id}",
        "comment_lists_url": f"/api/v1/revisions/{revision.id}/comment-lists",
    }


@router.get("/mdl-items/{item_id}/revisions", summary="تاریخچه بازنگری‌های یک مدرک")
def item_revisions(item_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    item = db.get(MDLItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="مدرک یافت نشد.")
    acl.require_project(db, user, item.project_id)
    timeline = document_service.timeline(db, item.id)
    return {
        "item": {
            "id": str(item.id),
            "doc_code": item.doc_code,
            "title": item.title,
            "current_rev": item.current_rev,
            "status": item.status,
            "discipline": item.discipline.code if item.discipline else None,
            "unit": item.unit.code if item.unit else None,
            "due_date": item.due_date.isoformat() if item.due_date else None,
        },
        "revisions": timeline,
    }


@router.get(
    "/mdl-items/{item_id}/document-records",
    response_model=list[DocumentRecordOut],
    summary="تاریخچه کامل Document Records یک مدرک",
)
def item_document_records(
    item_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> list[DocumentRecordOut]:
    """Return every persisted revision of one unique Doc. No.

    The MDL item is the stable document identity; revisions are append-only
    children. We deliberately do not derive this list from the current
    transmittal lines, because doing so hides Void history as soon as a newer
    revision becomes current.
    """
    item = db.get(MDLItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="مدرک یافت نشد.")
    acl.require_project(db, user, item.project_id)
    revisions = list(
        db.execute(
            select(DocumentRevision)
            .where(DocumentRevision.mdl_item_id == item.id)
            .order_by(DocumentRevision.rev_seq.desc(), DocumentRevision.registered_at.desc())
        ).scalars()
    )
    if not revisions:
        return []

    revision_ids = [revision.id for revision in revisions]
    lines = list(
        db.execute(
            select(TransmittalLine)
            .join(Transmittal, TransmittalLine.transmittal_id == Transmittal.id)
            .where(
                TransmittalLine.revision_id.in_(revision_ids),
                Transmittal.project_id == item.project_id,
            )
        ).scalars()
    )
    visible_by_revision: dict[uuid.UUID, list[TransmittalLine]] = {}
    for line in lines:
        if acl.can_access_transmittal(db, user, line.transmittal):
            visible_by_revision.setdefault(line.revision_id, []).append(line)

    records: list[DocumentRecordOut] = []
    for revision in revisions:
        candidates = visible_by_revision.get(revision.id, [])
        # Prefer the newest issued/created transmittal when a revision was sent
        # more than once. The revision itself remains a single history row.
        candidates.sort(
            key=lambda line: (
                bool(line.transmittal.issued_at),
                (line.transmittal.issued_at or line.transmittal.created_at).isoformat(),
            ),
            reverse=True,
        )
        line = candidates[0] if candidates else None
        transmittal = line.transmittal if line is not None else None
        assigned: list[str] = []
        flow_count = 0
        if transmittal is not None:
            assigned.extend(
                recipient.user.full_name
                for recipient in transmittal.recipients
                if recipient.user is not None
            )
            assigned.extend(
                referral.to_user.full_name
                for referral in transmittal.referrals
                if referral.to_user is not None and referral.to_user.full_name not in assigned
            )
            flow_count = len(transmittal.referrals) + len(
                db.execute(
                    select(AuditLog.id).where(
                        AuditLog.project_id == item.project_id,
                        AuditLog.entity_type == "transmittal",
                        AuditLog.entity_id == str(transmittal.id),
                    )
                ).scalars().all()
            )
        status_value = str(revision.status)
        if not revision.is_current:
            # Legacy rows may still carry ``superseded``; the business-facing
            # Document Records status is consistently rendered as Void.
            status_value = "void"
        records.append(
            DocumentRecordOut(
                id=revision.id,
                revision_id=revision.id,
                doc_code=item.doc_code,
                title=revision.title_as_drawn or item.title,
                transmittal_id=transmittal.id if transmittal is not None else None,
                transmittal_number=transmittal.number if transmittal is not None else None,
                transmittal_type=(
                    str(getattr(transmittal.type, "value", transmittal.type))
                    if transmittal is not None
                    else None
                ),
                transmittal_status=(str(transmittal.status) if transmittal is not None else None),
                discipline=(line.discipline_code if line and line.discipline_code else item.discipline.code if item.discipline else None),
                version=revision.rev,
                status=status_value,
                is_latest=bool(revision.is_current),
                chain_count=len(revisions),
                assigned_to="، ".join(dict.fromkeys(assigned)) or None,
                flow_history_count=flow_count,
                has_file=revision.source_file_id is not None,
            )
        )
    return records


@router.get("/revisions/{revision_id}/transmittals", response_model=list[LineOut], summary="برگه‌های ارسال مدرک")
def revision_transmittals(revision_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[LineOut]:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    acl.require_project(db, user, revision.project_id)
    lines = db.execute(
        select(TransmittalLine).where(TransmittalLine.revision_id == revision_id)
    ).scalars()
    extraction_status = None
    if revision.extraction_result_id:
        extraction = db.get(ExtractionResult, revision.extraction_result_id)
        extraction_status = extraction.doc_status if extraction else None
    return [
        LineOut(
            id=line.id,
            line_no=line.line_no,
            revision_id=line.revision_id,
            mdl_item_id=revision.mdl_item.id if revision.mdl_item else None,
            action=str(line.action),
            status=str(line.status),
            doc_status=(line.doc_status or (revision.mdl_item.status if revision.mdl_item and revision.mdl_item.status else extraction_status)),
            doc_percent=line.doc_percent,
            poi=revision.mdl_item.poi if revision.mdl_item else None,
            due_date=line.due_date,
            note=line.note,
            doc_code=revision.mdl_item.doc_code if revision.mdl_item else None,
            title=revision.mdl_item.title if revision.mdl_item else None,
            rev=revision.rev,
            page_count=revision.page_count,
            discipline_code=line.discipline_code,
        )
        for line in lines
    ]


@router.get("/projects/{project_id}/transmittal-candidates", summary="مدارک آماده ارسال (بدون برگه باز)")
def transmittal_candidates(
    project_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    project = acl.require_project(db, user, project_id)
    issued_line_revisions = select(TransmittalLine.revision_id).join(
        Transmittal, TransmittalLine.transmittal_id == Transmittal.id
    ).where(Transmittal.project_id == project.id, Transmittal.status != "cancelled")
    rows = db.execute(
        select(DocumentRevision)
        .where(
            DocumentRevision.project_id == project.id,
            DocumentRevision.is_current.is_(True),
            ~DocumentRevision.id.in_(issued_line_revisions),
        )
        .order_by(DocumentRevision.registered_at.desc())
        .limit(limit)
    ).scalars()
    return {
        "items": [
            {
                "revision_id": str(rev.id),
                "doc_code": rev.mdl_item.doc_code if rev.mdl_item else None,
                "title": rev.mdl_item.title if rev.mdl_item else None,
                "rev": rev.rev,
                "discipline": rev.mdl_item.discipline.code if rev.mdl_item and rev.mdl_item.discipline else None,
                "registered_at": rev.registered_at.isoformat() if rev.registered_at else None,
            }
            for rev in rows
        ]
    }
