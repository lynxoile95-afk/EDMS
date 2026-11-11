"""Comment List: registration, authenticated attachments, replies and closure."""

from __future__ import annotations

import csv
import io
import mimetypes
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.models import (
    Comment,
    CommentAttachment,
    CommentList,
    Discipline,
    DocumentRevision,
    NumberSequence,
    User,
)
from app.models.enums import CommentDisposition, CommentStatus
from app.schemas.transmittals import (
    CommentAttachmentOut,
    CommentClose,
    CommentCreate,
    CommentListOut,
    CommentOut,
    CommentRespond,
)
from app.services import acl, audit, notification, transmittal_service
from app.services.storage import build_key, get_storage
from app.utils.jalali import format_jalali

router = APIRouter(tags=["comments"])

# These are deliberately in one policy block. The settings values can be
# changed per installation, while the route remains the single enforcement
# point for both the create and edit forms.
COMMENT_ATTACHMENT_MAX_BYTES = settings.comment_attachment_max_mb * 1024 * 1024
COMMENT_ATTACHMENT_ALLOWED_EXTENSIONS = settings.comment_attachment_extensions
COORDINATOR_ROLE_NAMES = {
    "coordinator",
    "dcc",
    "project_control",
    "pmo",
    "project_manager",
    "system_admin",
}
def _comment_attachment_out(attachment: CommentAttachment) -> CommentAttachmentOut:
    return CommentAttachmentOut(
        id=attachment.id,
        comment_id=attachment.comment_id,
        file_name=attachment.file_name,
        file_size=attachment.file_size,
        mime_type=attachment.mime_type,
        uploaded_by_id=attachment.uploaded_by_id,
        uploaded_by_name=attachment.uploaded_by.full_name if attachment.uploaded_by else None,
        uploaded_at=attachment.uploaded_at,
        download_url=(
            f"{settings.api_v1_prefix}/comments/{attachment.comment_id}/attachments/"
            f"{attachment.id}/download"
        ),
    )


def _comment_out(comment: Comment) -> CommentOut:
    revision = comment.revision
    return CommentOut(
        id=comment.id,
        registration_id=str(comment.id),
        revision_id=comment.revision_id,
        comment_list_id=comment.comment_list_id,
        seq_no=comment.seq_no,
        code=str(comment.code),
        category=comment.category,
        related_comment_id=comment.related_comment_id,
        discipline_id=comment.discipline_id,
        discipline_code=comment.discipline.code if comment.discipline else None,
        discipline_name=comment.discipline.name if comment.discipline else None,
        text=comment.text,
        page_no=comment.page_no,
        from_page_no=comment.from_page_no,
        to_page_no=comment.to_page_no,
        bbox=comment.bbox or [],
        status=str(comment.status),
        disposition=str(comment.disposition) if comment.disposition else None,
        status_change_date=comment.status_change_date,
        personal_remarks=comment.personal_remarks,
        coordinator_date=comment.coordinator_date,
        judgment=comment.judgment,
        judgment_comments=comment.judgment_comments,
        remarks=comment.remarks,
        response_text=comment.response_text,
        responded_at=comment.responded_at,
        author_id=comment.author_id,
        author_name=comment.author.full_name if comment.author else None,
        created_at=comment.created_at,
        revision_time=(getattr(revision, "registered_at", None) or getattr(revision, "created_at", None))
        if revision
        else None,
        attachments=[_comment_attachment_out(item) for item in (comment.attachments or [])],
        attachment_count=len(comment.attachments or []),
    )


def _coordinator_can_edit(db, user: User, project_id: uuid.UUID) -> bool:
    if user.is_superuser:
        return True
    roles = {role.strip().lower().replace(" ", "_") for role in acl.roles_in(db, user, project_id)}
    return bool(roles & COORDINATOR_ROLE_NAMES) or any("coordinator" in role for role in roles)


def _assert_comment_editor(db, user: User, project_id: uuid.UUID, comment: Comment) -> None:
    # A comment is immutable for its author after registration. The coordinator
    # is the only role allowed to reopen the registration form and fill the
    # coordinator judgment/comments band.
    if _coordinator_can_edit(db, user, project_id):
        return
    raise HTTPException(
        status_code=403,
        detail="پس از ثبت نظر، فقط هماهنگ‌کننده می‌تواند نظر هماهنگ‌کننده را ثبت کند.",
    )


def _assert_attachment_editable(comment: Comment) -> None:
    # Attachments are evidence of the original registration. They may be
    # viewed/downloaded after registration, but never appended or removed.
    raise HTTPException(status_code=409, detail="پس از ثبت نظر، پیوست آن قابل افزودن یا حذف نیست.")


def _validate_page_range(from_page_no: int | None, to_page_no: int | None) -> None:
    if from_page_no is not None and from_page_no < 1:
        raise HTTPException(status_code=422, detail="شماره صفحه شروع باید حداقل ۱ باشد.")
    if to_page_no is not None and to_page_no < 1:
        raise HTTPException(status_code=422, detail="شماره صفحه پایان باید حداقل ۱ باشد.")
    if from_page_no is not None and to_page_no is not None and to_page_no < from_page_no:
        raise HTTPException(status_code=422, detail="صفحه پایان باید بزرگ‌تر یا مساوی صفحه شروع باشد.")


def _validate_related_fields(
    db,
    revision: DocumentRevision,
    related_comment_id: uuid.UUID | None,
    discipline_id: uuid.UUID | None,
    discipline_code: str | None = None,
) -> uuid.UUID | None:
    if related_comment_id is not None:
        related = db.get(Comment, related_comment_id)
        if related is None or related.revision_id != revision.id:
            raise HTTPException(status_code=422, detail="نظر مرتبط باید از همین بازنگری انتخاب شود.")
    if discipline_code and not discipline_id:
        discipline = db.execute(
            select(Discipline).where(
                Discipline.project_id == revision.project_id,
                func.lower(Discipline.code) == discipline_code.strip().lower(),
            )
        ).scalar_one_or_none()
        if discipline is None:
            raise HTTPException(status_code=422, detail="کد رشته انتخاب‌شده در این پروژه یافت نشد.")
        discipline_id = discipline.id
    if discipline_id is not None:
        # The model relationship is intentionally not relied on for validation:
        # the project scope is authoritative and avoids cross-project IDs.
        discipline = db.get(Discipline, discipline_id)
        if discipline is None or discipline.project_id != revision.project_id:
            raise HTTPException(status_code=422, detail="رشته انتخاب‌شده متعلق به پروژه این مدرک نیست.")
    return discipline_id


def _next_comment_no(db, project_id: uuid.UUID, comment_list_id: uuid.UUID) -> int:
    """Allocate a comment number from a row-locked, persistent counter.

    The counter is seeded from existing comments, so adding this feature never
    renumbers legacy rows. The unique constraint on comments is a final guard
    if a database races before a newly-created counter row becomes visible.
    """
    scope_key = f"comment-list:{comment_list_id}"
    for _ in range(8):
        sequence = db.execute(
            select(NumberSequence)
            .where(NumberSequence.project_id == project_id, NumberSequence.scope_key == scope_key)
            .with_for_update()
        ).scalar_one_or_none()
        if sequence is None:
            current = (
                db.execute(
                    select(func.coalesce(func.max(Comment.seq_no), 0)).where(
                        Comment.comment_list_id == comment_list_id
                    )
                ).scalar_one()
                or 0
            )
            candidate = NumberSequence(project_id=project_id, scope_key=scope_key, next_value=int(current) + 1)
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                sequence = candidate
            except IntegrityError:
                continue
        value = int(sequence.next_value)
        sequence.next_value = value + 1
        db.flush()
        return value
    raise HTTPException(status_code=409, detail="تخصیص شماره نظر هم‌زمان بود؛ دوباره تلاش کنید.")


def _ensure_comment_list(db, revision_id: uuid.UUID) -> CommentList:
    comment_list = db.execute(
        select(CommentList).where(CommentList.revision_id == revision_id).order_by(CommentList.opened_at.desc())
    ).scalars().first()
    if comment_list is None:
        comment_list = transmittal_service.ensure_comment_list(db, revision_id=revision_id, transmittal_id=None)
        db.flush()
    return comment_list


def _new_comment(
    db,
    revision: DocumentRevision,
    user: User,
    *,
    text: str,
    code: str,
    category: str | None,
    related_comment_id: uuid.UUID | None,
    discipline_id: uuid.UUID | None,
    discipline_code: str | None,
    page_no: int | None,
    from_page_no: int | None,
    to_page_no: int | None,
    disposition: str | None,
    status_change_date: date | None,
    personal_remarks: str | None,
    coordinator_date: date | None,
    judgment: str | None,
    judgment_comments: str | None,
    remarks: str | None,
    coordinator: bool,
) -> Comment:
    text = text.strip()
    if len(text) < 2:
        raise HTTPException(status_code=422, detail="شرح نظر اجباری است و باید حداقل دو نویسه داشته باشد.")
    if code not in {"1", "2", "3"}:
        raise HTTPException(status_code=422, detail="کد نظر نامعتبر است.")
    if disposition and disposition not in {item.value for item in CommentDisposition}:
        raise HTTPException(status_code=422, detail="وضعیت نظر نامعتبر است.")
    _validate_page_range(from_page_no, to_page_no)
    discipline_id = _validate_related_fields(db, revision, related_comment_id, discipline_id, discipline_code)
    comment_list = _ensure_comment_list(db, revision.id)
    seq = _next_comment_no(db, revision.project_id, comment_list.id)
    comment = Comment(
        comment_list_id=comment_list.id,
        revision_id=revision.id,
        author_id=user.id,
        seq_no=seq,
        code=code,
        category=category.strip() if category else None,
        text=text,
        page_no=page_no or from_page_no,
        from_page_no=from_page_no,
        to_page_no=to_page_no,
        related_comment_id=related_comment_id,
        discipline_id=discipline_id,
        status=CommentStatus.OPEN,
        disposition=disposition,
        status_change_date=status_change_date,
        personal_remarks=personal_remarks.strip() if personal_remarks else None,
        # A non-coordinator may see the coordinator band but cannot submit values
        # into it; the server repeats that rule instead of trusting disabled HTML.
        coordinator_date=coordinator_date if coordinator else None,
        judgment=(judgment or "draft").strip() if coordinator else "draft",
        judgment_comments=judgment_comments.strip() if coordinator and judgment_comments else None,
        remarks=remarks.strip() if coordinator and remarks else None,
    )
    db.add(comment)
    db.flush()
    return comment


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes, str]]:
    """Read and validate all files before persisting any of them."""
    prepared: list[tuple[str, bytes, str]] = []
    for upload in files:
        filename = Path(upload.filename or "").name.strip()
        extension = Path(filename).suffix.lower().lstrip(".")
        if not filename or not extension or extension not in COMMENT_ATTACHMENT_ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(COMMENT_ATTACHMENT_ALLOWED_EXTENSIONS)).upper()
            raise HTTPException(status_code=415, detail=f"فرمت فایل مجاز نیست. فرمت‌های مجاز: {allowed}.")
        data = await upload.read(COMMENT_ATTACHMENT_MAX_BYTES + 1)
        if not data:
            raise HTTPException(status_code=422, detail=f"فایل «{filename}» خالی است.")
        if len(data) > COMMENT_ATTACHMENT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"حجم فایل «{filename}» بیشتر از حد مجاز ({settings.comment_attachment_max_mb} مگابایت) است.",
            )
        mime_type = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        prepared.append((filename[:512], data, mime_type[:255]))
    return prepared


def _store_attachments(
    db,
    comment: Comment,
    user: User,
    prepared: list[tuple[str, bytes, str]],
) -> list[str]:
    storage = get_storage()
    keys: list[str] = []
    for filename, data, mime_type in prepared:
        suffix = Path(filename).suffix.lower()
        key = build_key("comment-attachments", str(comment.id), f"{uuid.uuid4()}{suffix}")
        storage.put_bytes(key, data, content_type=mime_type)
        keys.append(key)
        db.add(
            CommentAttachment(
                comment_id=comment.id,
                file_name=filename,
                file_path=key,
                file_size=len(data),
                mime_type=mime_type,
                uploaded_by_id=user.id,
            )
        )
    if prepared:
        db.flush()
    return keys


def _queue_comment_notification(db, revision: DocumentRevision, project, user: User, comment: Comment) -> None:
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
            "text": comment.text[:500],
            "link": f"{settings_url()}/documents/{revision.id}",
        },
        project_id=project.id,
        entity_type="comment",
        entity_id=str(comment.id),
    )


def _commit_comment_create(db, revision: DocumentRevision, project, user: User, comment: Comment) -> None:
    _queue_comment_notification(db, revision, project, user, comment)
    audit.record(
        db,
        action="comment.create",
        entity_type="comment",
        entity_id=comment.id,
        actor=user,
        project_id=project.id,
        after={"code": comment.code, "revision": str(revision.id), "seq_no": comment.seq_no},
    )
    db.commit()
    db.refresh(comment)


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
def add_comment(revision_id: uuid.UUID, payload: CommentCreate, db: DbSession, user: CurrentUser) -> CommentOut:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    project = acl.require_project(db, user, revision.project_id)
    acl.assert_can_review(db, user, project.id)
    comment = _new_comment(
        db,
        revision,
        user,
        text=payload.text,
        code=payload.code,
        category=payload.category,
        related_comment_id=payload.related_comment_id,
        discipline_id=payload.discipline_id,
        discipline_code=payload.discipline_code,
        page_no=payload.page_no,
        from_page_no=payload.from_page_no,
        to_page_no=payload.to_page_no,
        disposition=payload.disposition.value if payload.disposition else None,
        status_change_date=payload.status_change_date,
        personal_remarks=payload.personal_remarks,
        coordinator_date=payload.coordinator_date,
        judgment=payload.judgment,
        judgment_comments=payload.judgment_comments,
        remarks=payload.remarks,
        coordinator=_coordinator_can_edit(db, user, project.id),
    )
    _commit_comment_create(db, revision, project, user, comment)
    return _comment_out(comment)


@router.post(
    "/revisions/{revision_id}/comments/multipart",
    response_model=CommentOut,
    status_code=201,
    summary="ثبت نظر و پیوست‌های آن با multipart/form-data",
)
async def add_comment_multipart(
    revision_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    text: str = Form(...),
    code: str = Form("2"),
    category: str | None = Form(default=None),
    related_comment_id: uuid.UUID | None = Form(default=None),
    discipline_id: uuid.UUID | None = Form(default=None),
    discipline_code: str | None = Form(default=None),
    page_no: int | None = Form(default=None),
    from_page_no: int | None = Form(default=None),
    to_page_no: int | None = Form(default=None),
    disposition: str | None = Form(default=None),
    status_change_date: date | None = Form(default=None),
    personal_remarks: str | None = Form(default=None),
    coordinator_date: date | None = Form(default=None),
    judgment: str = Form("draft"),
    judgment_comments: str | None = Form(default=None),
    remarks: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> CommentOut:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="بازنگری یافت نشد.")
    project = acl.require_project(db, user, revision.project_id)
    acl.assert_can_review(db, user, project.id)
    prepared = await _read_uploads(files)
    comment = _new_comment(
        db,
        revision,
        user,
        text=text,
        code=code,
        category=category,
        related_comment_id=related_comment_id,
        discipline_id=discipline_id,
        discipline_code=discipline_code,
        page_no=page_no,
        from_page_no=from_page_no,
        to_page_no=to_page_no,
        disposition=disposition,
        status_change_date=status_change_date,
        personal_remarks=personal_remarks,
        coordinator_date=coordinator_date,
        judgment=judgment,
        judgment_comments=judgment_comments,
        remarks=remarks,
        coordinator=_coordinator_can_edit(db, user, project.id),
    )
    keys: list[str] = []
    try:
        keys = _store_attachments(db, comment, user, prepared)
        _commit_comment_create(db, revision, project, user, comment)
    except Exception:
        db.rollback()
        for key in keys:
            get_storage().delete(key)
        raise
    return _comment_out(comment)


@router.patch("/comments/{comment_id}", response_model=CommentOut, summary="ثبت نظر هماهنگ‌کننده روی نظر موجود")
async def update_comment(
    comment_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    text: str = Form(...),
    code: str = Form("2"),
    category: str | None = Form(default=None),
    related_comment_id: uuid.UUID | None = Form(default=None),
    discipline_id: uuid.UUID | None = Form(default=None),
    discipline_code: str | None = Form(default=None),
    page_no: int | None = Form(default=None),
    from_page_no: int | None = Form(default=None),
    to_page_no: int | None = Form(default=None),
    disposition: str | None = Form(default=None),
    status_change_date: date | None = Form(default=None),
    personal_remarks: str | None = Form(default=None),
    coordinator_date: date | None = Form(default=None),
    judgment: str = Form("draft"),
    judgment_comments: str | None = Form(default=None),
    remarks: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> CommentOut:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="نظر یافت نشد.")
    revision = db.get(DocumentRevision, comment.revision_id)
    project = acl.require_project(db, user, revision.project_id)
    _assert_comment_editor(db, user, project.id, comment)
    if files:
        raise HTTPException(status_code=409, detail="پس از ثبت نظر، افزودن پیوست مجاز نیست.")

    # The original registration fields above remain accepted for compatibility
    # with older clients, but they are deliberately ignored. A coordinator may
    # only fill the coordinator-owned fields below; the author's text, code,
    # pages, disposition and existing evidence cannot be changed.
    comment.coordinator_date = coordinator_date
    comment.judgment = judgment.strip() or "draft"
    comment.judgment_comments = judgment_comments.strip() if judgment_comments else None
    comment.remarks = remarks.strip() if remarks else None
    try:
        audit.record(
            db,
            action="comment.coordinator_update",
            entity_type="comment",
            entity_id=comment.id,
            actor=user,
            project_id=project.id,
        )
        db.commit()
        db.refresh(comment)
    except Exception:
        db.rollback()
        raise
    return _comment_out(comment)


@router.delete(
    "/comments/{comment_id}/attachments/{attachment_id}",
    status_code=204,
    summary="حذف پیوست نظر پیش از نهایی‌شدن",
)
def delete_comment_attachment(
    comment_id: uuid.UUID, attachment_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="نظر یافت نشد.")
    revision = db.get(DocumentRevision, comment.revision_id)
    project = acl.require_project(db, user, revision.project_id)
    _assert_comment_editor(db, user, project.id, comment)
    _assert_attachment_editable(comment)
    attachment = db.get(CommentAttachment, attachment_id)
    if attachment is None or attachment.comment_id != comment.id:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    key = attachment.file_path
    db.delete(attachment)
    audit.record(
        db,
        action="comment.attachment.delete",
        entity_type="comment_attachment",
        entity_id=attachment.id,
        actor=user,
        project_id=project.id,
    )
    db.commit()
    # Test clients and a few embedded deployments intentionally reuse one
    # SQLAlchemy session across requests. Expire the parent collection so a
    # just-deleted attachment cannot remain visible from the identity map.
    db.expire(comment, ["attachments"])
    try:
        get_storage().delete(key)
    except Exception:
        # Database state is authoritative; a missing object is safe to retry via
        # storage maintenance and must not turn a successful deletion into a 500.
        pass
    return Response(status_code=204)


@router.get(
    "/comments/{comment_id}/attachments/{attachment_id}/download",
    summary="مشاهده یا دانلود پیوست احراز هویت‌شده نظر",
)
def download_comment_attachment(
    comment_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    inline: bool = Query(default=False),
) -> Response:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="نظر یافت نشد.")
    revision = db.get(DocumentRevision, comment.revision_id)
    acl.require_project(db, user, revision.project_id)
    attachment = db.get(CommentAttachment, attachment_id)
    if attachment is None or attachment.comment_id != comment.id:
        raise HTTPException(status_code=404, detail="پیوست یافت نشد.")
    try:
        payload = get_storage().get_bytes(attachment.file_path)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="فایل پیوست در مخزن پیدا نشد.") from None
    disposition = "inline" if inline else "attachment"
    safe_name = quote(attachment.file_name, safe=".")
    return Response(
        content=payload,
        media_type=attachment.mime_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{safe_name}",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
    return settings.public_base_url
