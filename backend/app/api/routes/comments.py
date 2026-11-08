"""Comment List: reviewer remarks, originator responses, closure."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models import Comment, CommentList, DocumentRevision, MDLItem, User
from app.models.enums import CommentDisposition, CommentStatus, ProjectRole
from app.schemas.transmittals import CommentClose, CommentCreate, CommentListOut, CommentOut, CommentRespond
from app.services import acl, audit, notification, transmittal_service
from app.utils.jalali import format_jalali

router = APIRouter(tags=["comments"])


def _comment_out(comment: Comment) -> CommentOut:
    return CommentOut(
        id=comment.id,
        revision_id=comment.revision_id,
        comment_list_id=comment.comment_list_id,
        seq_no=comment.seq_no,
        code=str(comment.code),
        category=comment.category,
        text=comment.text,
        page_no=comment.page_no,
        bbox=comment.bbox or [],
        status=str(comment.status),
        disposition=str(comment.disposition) if comment.disposition else None,
        response_text=comment.response_text,
        responded_at=comment.responded_at,
        author_name=comment.author.full_name if comment.author else None,
        created_at=comment.created_at,
    )


@router.get(
    "/revisions/{revision_id}/comment-lists",
    response_model=list[CommentListOut],
    summary="فهرست نظرها (Comment List) یک بازنگری",
)
def list_comment_lists(revision_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[CommentListOut]:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    acl.require_project(db, user, revision.project_id)
    lists = db.execute(
        select(CommentList).where(CommentList.revision_id == revision_id).order_by(CommentList.opened_at)
    ).scalars()
    return [
        CommentListOut(
            id=item.id,
            revision_id=item.revision_id,
            transmittal_id=item.transmittal_id,
            status=item.status,
            opened_at=item.opened_at,
            closed_at=item.closed_at,
            comments=[_comment_out(c) for c in item.comments],
        )
        for item in lists
    ]


@router.post(
    "/revisions/{revision_id}/comments",
    response_model=CommentOut,
    status_code=201,
    summary="ثبت نظر کارشناس روی مدرک",
)
def add_comment(
    revision_id: uuid.UUID, payload: CommentCreate, db: DbSession, user: CurrentUser
) -> CommentOut:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    project = acl.require_project(db, user, revision.project_id)
    acl.assert_can_review(db, user, project.id)

    comment_list = db.execute(
        select(CommentList).where(CommentList.revision_id == revision_id).order_by(CommentList.opened_at.desc())
    ).scalars().first()
    if comment_list is None:
        comment_list = transmittal_service.ensure_comment_list(
            db, revision_id=revision_id, transmittal_id=None
        )
    seq = (
        db.execute(
            select(func.coalesce(func.max(Comment.seq_no), 0)).where(
                Comment.comment_list_id == comment_list.id
            )
        ).scalar_one()
        or 0
    ) + 1

    comment = Comment(
        comment_list_id=comment_list.id,
        revision_id=revision_id,
        author_id=user.id,
        seq_no=seq,
        code=payload.code,
        category=payload.category,
        text=payload.text,
        page_no=payload.page_no,
        bbox=payload.bbox,
        status=CommentStatus.OPEN,
    )
    db.add(comment)
    db.flush()

    mdl_item = revision.mdl_item
    owner = db.get(User, mdl_item.responsible_id) if mdl_item and mdl_item.responsible_id else None
    notification.queue(
        db,
        user=owner,
        template="comment.added",
        context={
            "doc_code": mdl_item.doc_code if mdl_item else "—",
            "rev": revision.rev,
            "author": user.full_name,
            "text": payload.text[:500],
            "link": f"{settings_url()}/documents/{revision.id}",
        },
        project_id=project.id,
        entity_type="comment",
        entity_id=str(comment.id),
    )
    audit.record(
        db, action="comment.create", entity_type="comment", entity_id=comment.id, actor=user,
        project_id=project.id, after={"code": payload.code, "revision": str(revision_id)},
    )
    db.commit()
    db.refresh(comment)
    return _comment_out(comment)


@router.post("/comments/{comment_id}/respond", response_model=CommentOut, summary="پاسخ به نظر")
def respond(comment_id: uuid.UUID, payload: CommentRespond, db: DbSession, user: CurrentUser) -> CommentOut:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="نظر یافت نشد.")
    revision = db.get(DocumentRevision, comment.revision_id)
    project = acl.require_project(db, user, revision.project_id)
    comment.response_text = payload.response_text
    comment.responded_by_id = user.id
    comment.responded_at = datetime.now(timezone.utc)
    comment.status = CommentStatus.ANSWERED
    if comment.author_id:
        notification.queue(
            db,
            user=db.get(User, comment.author_id),
            template="comment.answered",
            context={
                "doc_code": revision.mdl_item.doc_code if revision.mdl_item else "—",
                "text": payload.response_text[:500],
                "link": f"{settings_url()}/documents/{revision.id}",
            },
            project_id=project.id,
            entity_type="comment",
            entity_id=str(comment.id),
        )
    audit.record(
        db, action="comment.respond", entity_type="comment", entity_id=comment.id, actor=user,
        project_id=project.id,
    )
    db.commit()
    db.refresh(comment)
    return _comment_out(comment)


@router.post("/comments/{comment_id}/close", response_model=CommentOut, summary="بستن نظر")
def close(
    comment_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    payload: CommentClose | None = None,
) -> CommentOut:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="نظر یافت نشد.")
    revision = db.get(DocumentRevision, comment.revision_id)
    project = acl.require_project(db, user, revision.project_id)
    acl.assert_can_review(db, user, project.id)
    # `status` is loaded as a plain string, so compare by value — an identity
    # check against the StrEnum member is silently always False and made this
    # guard dead code.
    if comment.status == CommentStatus.OPEN:
        raise HTTPException(status_code=409, detail="نظر بدون پاسخ قابل بستن نیست.")
    # A closed comment keeps its first disposition: re-closing is a no-op on it.
    if comment.status != CommentStatus.CLOSED:
        comment.disposition = str(
            payload.disposition if payload is not None else CommentDisposition.ACCEPTED
        )
    comment.status = CommentStatus.CLOSED
    comment.closed_at = comment.closed_at or datetime.now(timezone.utc)
    comment_list = db.get(CommentList, comment.comment_list_id)
    if comment_list is not None:
        remaining = db.execute(
            select(func.count(Comment.id)).where(
                Comment.comment_list_id == comment_list.id, Comment.status != CommentStatus.CLOSED
            )
        ).scalar_one()
        if remaining == 0:
            comment_list.status = "closed"
            comment_list.closed_at = datetime.now(timezone.utc)
    audit.record(
        db, action="comment.close", entity_type="comment", entity_id=comment.id, actor=user,
        project_id=project.id,
    )
    db.commit()
    db.refresh(comment)
    return _comment_out(comment)


@router.get("/revisions/{revision_id}/comments/export", summary="خروجی CSV فهرست نظرها")
def export_comments(revision_id: uuid.UUID, db: DbSession, user: CurrentUser) -> StreamingResponse:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    acl.require_project(db, user, revision.project_id)
    comments = db.execute(
        select(Comment).where(Comment.revision_id == revision_id).order_by(Comment.seq_no)
    ).scalars()

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM so Excel opens Persian correctly
    writer = csv.writer(buffer)
    writer.writerow(["#", "Code", "Page", "Comment", "Status", "Response", "Author", "Date (Jalali)"])
    for comment in comments:
        writer.writerow(
            [
                comment.seq_no,
                comment.code,
                comment.page_no or "",
                comment.text,
                comment.status,
                comment.response_text or "",
                comment.author.full_name if comment.author else "",
                format_jalali(comment.created_at),
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="comment-list-{revision.rev}.csv"'},
    )


def settings_url() -> str:
    from app.core.config import settings

    return settings.public_base_url
