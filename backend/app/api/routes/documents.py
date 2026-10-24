"""Registered documents, revisions and their history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models import DocumentRevision, MDLItem, Transmittal, TransmittalLine
from app.schemas.transmittals import LineOut
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


@router.get("/revisions/{revision_id}/transmittals", response_model=list[LineOut], summary="برگه‌های ارسال مدرک")
def revision_transmittals(revision_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[LineOut]:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    acl.require_project(db, user, revision.project_id)
    lines = db.execute(
        select(TransmittalLine).where(TransmittalLine.revision_id == revision_id)
    ).scalars()
    return [
        LineOut(
            id=line.id,
            line_no=line.line_no,
            revision_id=line.revision_id,
            action=str(line.action),
            status=str(line.status),
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
