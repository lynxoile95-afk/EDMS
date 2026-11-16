"""Lifecycle and authorization service for PDF review markups."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    Comment,
    CommentAttachment,
    MarkupAnnotation,
    MarkupAnnotationStyle,
    MarkupSession,
    NumberSequence,
    Project,
    Transmittal,
    TransmittalLine,
    User,
)
from app.models.enums import CommentStatus, ProjectRole
from app.schemas.markups import (
    MarkupAnnotationOut,
    MarkupAnnotationsSave,
    MarkupCreate,
    MarkupOutputOut,
    MarkupReply,
    MarkupSessionOut,
    MarkupSubmitOut,
)
from app.services import acl, audit, transmittal_service
from app.services.markup_rendering import (
    output_filenames,
    render_annotated_pdf,
    render_comment_sheet,
)
from app.services.storage import build_key, get_storage

DEFAULT_MARKUP_STROKE_WIDTH = 2.5
MIN_MARKUP_STROKE_WIDTH = 1.0
MAX_MARKUP_STROKE_WIDTH = 6.0


def _safe_stroke_width(value: float | None) -> float:
    try:
        width = float(value if value is not None else DEFAULT_MARKUP_STROKE_WIDTH)
    except (TypeError, ValueError):
        return DEFAULT_MARKUP_STROKE_WIDTH
    return min(max(width, MIN_MARKUP_STROKE_WIDTH), MAX_MARKUP_STROKE_WIDTH)


def _ensure_enabled() -> None:
    if not settings.review_markup_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="قابلیت بازبینی نشانه‌گذاری فعال نیست.")


def _access(db: Session, user: User, transmittal: Transmittal) -> Project:
    _ensure_enabled()
    return acl.require_transmittal(db, user, transmittal)


def _capability(db: Session, user: User, transmittal: Transmittal) -> Project:
    project = _access(db, user, transmittal)
    acl.assert_can_comment(db, user, project.id)
    return project


def _status(session: MarkupSession) -> str:
    return str(getattr(session.status, "value", session.status))


def _source_attachment(transmittal: Transmittal, file_id: uuid.UUID) -> dict[str, Any]:
    attachment = next(
        (item for item in (transmittal.meta or {}).get("attachments", []) if str(item.get("id")) == str(file_id)),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="پیوست PDF یافت نشد.")
    kind = str(attachment.get("kind") or "")
    content_type = str(attachment.get("content_type") or "").lower()
    filename = str(attachment.get("filename") or "")
    if kind != "pdf" and content_type != "application/pdf" and Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="بازبینی نشانه‌گذاری فقط برای پیوست‌های PDF مجاز است.")
    if not attachment.get("storage_key"):
        raise HTTPException(status_code=404, detail="فایل PDF در انبار اسناد ثبت نشده است.")
    return attachment


def _line_for_attachment(db: Session, transmittal: Transmittal, attachment: dict[str, Any]) -> TransmittalLine | None:
    line_id = attachment.get("line_id")
    if not line_id:
        return None
    try:
        line_uuid = uuid.UUID(str(line_id))
    except ValueError:
        return None
    return db.execute(
        select(TransmittalLine).where(
            TransmittalLine.id == line_uuid,
            TransmittalLine.transmittal_id == transmittal.id,
        )
    ).scalar_one_or_none()


def _source_bytes(attachment: dict[str, Any]) -> tuple[bytes, str]:
    storage = get_storage()
    try:
        data = storage.get_bytes(str(attachment["storage_key"]))
    except Exception as exc:  # noqa: BLE001 - do not expose storage details
        raise HTTPException(status_code=404, detail="فایل PDF در انبار اسناد قابل دریافت نیست.") from exc
    return data, hashlib.sha256(data).hexdigest()


def _page_count(data: bytes) -> int:
    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
        try:
            if document.needs_pass:
                raise HTTPException(status_code=422, detail="فایل PDF با رمز محافظت شده است و قابل بازبینی نیست.")
            return max(1, int(document.page_count))
        finally:
            document.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="فایل PDF معتبر یا قابل بازکردن نیست.") from exc


def _document_metadata(line: TransmittalLine | None) -> tuple[uuid.UUID | None, uuid.UUID | None, str | None, str | None]:
    if line is None or line.revision is None:
        return None, None, None, None
    revision = line.revision
    item = revision.mdl_item
    return (
        revision.source_file_id,
        revision.id,
        item.doc_code if item is not None else None,
        revision.rev,
    )


def _safe_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _annotation_bounds(points: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    xs = [min(max(float(point.get("x", 0.0)), 0.0), 1.0) for point in points]
    ys = [min(max(float(point.get("y", 0.0)), 0.0), 1.0) for point in points]
    x = min(xs)
    y = min(ys)
    return x, y, max(xs) - x, max(ys) - y


def _annotation_out(annotation: MarkupAnnotation) -> MarkupAnnotationOut:
    return MarkupAnnotationOut(
        id=annotation.id,
        number=annotation.number,
        kind=annotation.kind,  # type: ignore[arg-type]
        page_no=annotation.page_no,
        x=annotation.x,
        y=annotation.y,
        w=annotation.w,
        h=annotation.h,
        points=annotation.points or [],
        text=annotation.text,
        color=annotation.color,
        stroke_width=_safe_stroke_width(annotation.style.stroke_width if annotation.style else None),
        author_id=annotation.author_id,
        author_name=annotation.author_name,
        created_at=annotation.created_at,
        updated_at=annotation.updated_at,
        reply_status=annotation.reply_status,  # type: ignore[arg-type]
        reply_text=annotation.reply_text,
        replied_by_id=annotation.replied_by_id,
        replied_by_name=annotation.replied_by_name,
        replied_at=annotation.replied_at,
    )


def _can_reply(db: Session, user: User, transmittal: Transmittal) -> bool:
    if user.is_superuser or user.id in {transmittal.created_by_id, transmittal.sender_id, transmittal.issued_by_id}:
        return True
    return acl.has_role(
        db,
        user,
        transmittal.project_id,
        ProjectRole.DCC,
        ProjectRole.PROJECT_CONTROL,
        ProjectRole.PMO,
        ProjectRole.PROJECT_MANAGER,
        ProjectRole.SYSTEM_ADMIN,
    )


def _session_out(db: Session, session: MarkupSession, user: User) -> MarkupSessionOut:
    transmittal = session.transmittal
    output_rows = [
        item
        for item in (transmittal.meta or {}).get("attachments", [])
        if str(item.get("markup_session_id") or "") == str(session.id)
    ]
    outputs = [
        MarkupOutputOut(
            attachment_id=uuid.UUID(str(item["id"])),
            filename=str(item.get("filename") or "markup.pdf"),
            kind="pdf" if item.get("related_type") == "markup_pdf" else "sheet",
            download_url=(
                f"{settings.api_v1_prefix}/transmittals/{session.transmittal_id}/attachments/"
                f"{item['id']}/download"
            ),
        )
        for item in output_rows
        if _safe_uuid(str(item.get("id") or "")) is not None
    ]
    return MarkupSessionOut(
        id=session.id,
        transmittal_id=session.transmittal_id,
        project_id=session.project_id,
        source_file_id=session.source_file_id,
        revision_id=session.revision_id,
        file_id=uuid.UUID(session.source_attachment_id),
        document_code=session.document_code,
        revision_label=session.revision_label,
        original_filename=session.original_filename,
        original_sha256=session.original_sha256,
        page_count=session.page_count,
        status=_status(session),  # type: ignore[arg-type]
        draft_version=session.draft_version,
        last_saved_at=session.last_saved_at,
        submitted_at=session.submitted_at,
        author_id=session.user_id,
        author_name=session.user.full_name if session.user else None,
        can_edit=_status(session) == "draft" and session.user_id == user.id,
        can_reply=_status(session) == "submitted" and _can_reply(db, user, transmittal),
        submitted_pdf_attachment_id=_safe_uuid(session.submitted_pdf_attachment_id),
        comment_sheet_attachment_id=_safe_uuid(session.comment_sheet_attachment_id),
        outputs=outputs,
        annotations=[_annotation_out(item) for item in session.annotations],
    )


def session_out(db: Session, session: MarkupSession, user: User) -> MarkupSessionOut:
    return _session_out(db, session, user)


def create_or_get(db: Session, user: User, payload: MarkupCreate) -> MarkupSession:
    transmittal = db.get(Transmittal, payload.transmittal_id)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
    project = _capability(db, user, transmittal)
    attachment = _source_attachment(transmittal, payload.file_id)
    line = _line_for_attachment(db, transmittal, attachment)
    source_file_id, revision_id, document_code, revision_label = _document_metadata(line)

    existing = db.execute(
        select(MarkupSession).where(
            MarkupSession.transmittal_id == transmittal.id,
            MarkupSession.source_attachment_id == str(payload.file_id),
            MarkupSession.user_id == user.id,
            MarkupSession.status == "draft",
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    source, digest = _source_bytes(attachment)
    page_count = _page_count(source)
    session = MarkupSession(
        transmittal_id=transmittal.id,
        project_id=project.id,
        source_file_id=source_file_id,
        revision_id=revision_id,
        user_id=user.id,
        source_attachment_id=str(payload.file_id),
        document_code=document_code,
        revision_label=revision_label,
        original_filename=os.path.basename(str(attachment.get("filename") or "document.pdf")),
        original_storage_key=str(attachment["storage_key"]),
        original_sha256=digest,
        page_count=page_count,
        status="draft",
        draft_version=0,
        generated_comment_ids=[],
    )
    db.add(session)
    db.flush()
    audit.record(
        db,
        action="markup.create",
        entity_type="markup_session",
        entity_id=session.id,
        actor=user,
        project_id=project.id,
        after={
            "transmittal_id": transmittal.id,
            "file_id": payload.file_id,
            "document_code": document_code,
            "revision": revision_label,
            "original_sha256": digest,
        },
    )
    return session


def get_authorized(db: Session, user: User, session_id: uuid.UUID) -> MarkupSession:
    session = db.get(MarkupSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="جلسه نشانه‌گذاری یافت نشد.")
    _access(db, user, session.transmittal)
    if _status(session) == "draft" and session.user_id != user.id and not user.is_superuser:
        raise acl.AccessDenied("پیش‌نویس جلسه فقط برای نویسنده آن قابل مشاهده است.")
    return session


def list_for_file(
    db: Session,
    user: User,
    file_id: uuid.UUID,
    transmittal_id: uuid.UUID | None = None,
) -> list[MarkupSession]:
    """List only sessions whose parent transmittal the caller may see."""
    stmt = select(MarkupSession).where(MarkupSession.source_attachment_id == str(file_id))
    if transmittal_id is not None:
        stmt = stmt.where(MarkupSession.transmittal_id == transmittal_id)
    rows = list(db.execute(stmt.order_by(MarkupSession.created_at.desc())).scalars())
    visible: list[MarkupSession] = []
    for session in rows:
        _access(db, user, session.transmittal)
        if _status(session) == "draft" and session.user_id != user.id and not user.is_superuser:
            continue
        visible.append(session)
    if transmittal_id is not None:
        transmittal = db.get(Transmittal, transmittal_id)
        if transmittal is None:
            raise HTTPException(status_code=404, detail="برگه ارسال یافت نشد.")
        _access(db, user, transmittal)
        _source_attachment(transmittal, file_id)
    else:
        # A caller may ask for a file before its first session exists.  Resolve
        # the legacy attachment owner and authorize it rather than returning an
        # ACL-blind empty list.
        for transmittal in db.execute(select(Transmittal)).scalars():
            if any(str(item.get("id")) == str(file_id) for item in (transmittal.meta or {}).get("attachments", [])):
                _access(db, user, transmittal)
                _source_attachment(transmittal, file_id)
    return visible


def save_annotations(db: Session, user: User, session: MarkupSession, payload: MarkupAnnotationsSave) -> MarkupSession:
    if _status(session) != "draft":
        raise HTTPException(status_code=409, detail="جلسه پس از ثبت نهایی فقط خواندنی است.")
    if session.user_id != user.id:
        raise acl.AccessDenied("فقط نویسنده جلسه می‌تواند پیش‌نویس نشانه‌گذاری را ویرایش کند.")
    _capability(db, user, session.transmittal)
    if payload.expected_version is not None and payload.expected_version != session.draft_version:
        raise HTTPException(status_code=409, detail="نسخه پیش‌نویس تغییر کرده است؛ ابتدا آخرین نسخه را بارگیری کنید.")

    seen_ids: set[uuid.UUID] = set()
    current = {annotation.id: annotation for annotation in session.annotations}
    for item in payload.annotations:
        if item.id is not None:
            if item.id in seen_ids or item.id not in current:
                raise HTTPException(status_code=422, detail="نشانه‌گذاری متعلق به این جلسه نیست.")
            seen_ids.add(item.id)
        if item.page_no > session.page_count:
            raise HTTPException(status_code=422, detail="شماره صفحه نشانه‌گذاری خارج از محدوده PDF است.")

    # Delete annotations omitted by the client, then update/create the rest.
    for annotation_id, annotation in list(current.items()):
        if annotation_id not in seen_ids:
            db.delete(annotation)
        else:
            # Free the old unique (session_id, number) values before applying
            # a reordered client list, e.g. [2, 1] -> [1, 2].
            annotation.number = -annotation.number
    db.flush()
    updated: list[MarkupAnnotation] = []
    for number, item in enumerate(payload.annotations, start=1):
        points = [point.model_dump() for point in item.points]
        x, y, w, h = _annotation_bounds(points)
        annotation = current.get(item.id) if item.id is not None else None
        if annotation is None:
            annotation = MarkupAnnotation(
                session_id=session.id,
                author_id=user.id,
                number=number,
                kind=item.kind,
                page_no=item.page_no,
                x=x,
                y=y,
                w=w,
                h=h,
                points=points,
                text=item.text,
                color=item.color,
                author_name=user.full_name,
                reply_status="open",
            )
            db.add(annotation)
        else:
            annotation.number = number
            annotation.kind = item.kind
            annotation.page_no = item.page_no
            annotation.x = x
            annotation.y = y
            annotation.w = w
            annotation.h = h
            annotation.points = points
            annotation.text = item.text
            annotation.color = item.color
        if annotation.style is None:
            annotation.style = MarkupAnnotationStyle(stroke_width=_safe_stroke_width(item.stroke_width))
        else:
            annotation.style.stroke_width = _safe_stroke_width(item.stroke_width)
        updated.append(annotation)
    session.draft_version += 1
    session.last_saved_at = datetime.now(timezone.utc)
    db.flush()
    audit.record(
        db,
        action="markup.save",
        entity_type="markup_session",
        entity_id=session.id,
        actor=user,
        project_id=session.project_id,
        after={"draft_version": session.draft_version, "annotation_count": len(updated)},
    )
    return session


def _next_comment_number(db: Session, project_id: uuid.UUID, comment_list_id: uuid.UUID) -> int:
    scope_key = f"comment-list:{comment_list_id}"
    sequence = db.execute(
        select(NumberSequence)
        .where(NumberSequence.project_id == project_id, NumberSequence.scope_key == scope_key)
        .with_for_update()
    ).scalar_one_or_none()
    if sequence is None:
        current = db.execute(
            select(func.coalesce(func.max(Comment.seq_no), 0)).where(Comment.comment_list_id == comment_list_id)
        ).scalar_one()
        sequence = NumberSequence(project_id=project_id, scope_key=scope_key, next_value=int(current or 0) + 1)
        db.add(sequence)
        db.flush()
    value = int(sequence.next_value)
    sequence.next_value = value + 1
    db.flush()
    return value


def _annotation_dict(annotation: MarkupAnnotation) -> dict[str, Any]:
    return {
        "number": annotation.number,
        "kind": annotation.kind,
        "page_no": annotation.page_no,
        "points": annotation.points or [],
        "text": annotation.text,
        "color": annotation.color,
        "stroke_width": _safe_stroke_width(annotation.style.stroke_width if annotation.style else None),
        "author_name": annotation.author_name,
        "reply_status": annotation.reply_status,
        "reply_text": annotation.reply_text,
        "created_at": annotation.created_at.isoformat() if annotation.created_at else None,
    }


def _attachment_record(
    *,
    attachment_id: uuid.UUID,
    transmittal: Transmittal,
    line: TransmittalLine | None,
    filename: str,
    content_type: str,
    data: bytes,
    user: User,
    storage_key: str,
    session_id: uuid.UUID,
    related_type: str,
) -> dict[str, Any]:
    return {
        "id": str(attachment_id),
        "line_id": str(line.id) if line is not None else None,
        "revision_id": str(line.revision_id) if line is not None else None,
        "kind": "comment",
        "related_type": related_type,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "storage_key": storage_key,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_by_id": str(user.id),
        "uploaded_by_name": user.full_name,
        "access_user_ids": [],
        "markup_session_id": str(session_id),
    }


def submit(db: Session, user: User, session: MarkupSession) -> MarkupSubmitOut:
    if _status(session) != "draft":
        raise HTTPException(status_code=409, detail="این جلسه قبلاً ثبت نهایی شده است.")
    if session.user_id != user.id:
        raise acl.AccessDenied("فقط نویسنده جلسه می‌تواند آن را ثبت نهایی کند.")
    project = _capability(db, user, session.transmittal)
    source, digest = _source_bytes(
        {"storage_key": session.original_storage_key}
    )
    if digest != session.original_sha256:
        raise HTTPException(status_code=409, detail="فایل اصلی تغییر کرده است؛ ثبت نشانه‌گذاری متوقف شد.")
    if _page_count(source) != session.page_count:
        raise HTTPException(status_code=409, detail="تعداد صفحات فایل اصلی تغییر کرده است؛ ثبت متوقف شد.")

    line = _line_for_attachment(
        db,
        session.transmittal,
        {"line_id": next(
            (
                item.get("line_id")
                for item in (session.transmittal.meta or {}).get("attachments", [])
                if str(item.get("id")) == session.source_attachment_id
            ),
            None,
        )},
    )
    if session.revision_id is None and line is not None:
        session.revision_id = line.revision_id
    if session.revision_id is None:
        raise HTTPException(status_code=422, detail="پیوست PDF به بازنگری مدرک متصل نیست.")

    annotations = list(session.annotations)
    raw_annotations = [_annotation_dict(annotation) for annotation in annotations]
    now = datetime.now(timezone.utc)
    pdf_filename, sheet_filename = output_filenames(session.original_filename, user.full_name, now)
    annotated_pdf = render_annotated_pdf(source, raw_annotations)
    sheet_pdf = render_comment_sheet(
        project_name=project.name,
        transmittal_number=session.transmittal.number,
        document_code=session.document_code,
        revision_label=session.revision_label,
        original_filename=session.original_filename,
        author_name=user.full_name,
        annotations=raw_annotations,
        submitted_at=now,
    )

    storage = get_storage()
    generated: list[tuple[uuid.UUID, str, bytes]] = []
    attachments: list[dict[str, Any]] = []
    try:
        for filename, data, related_type in (
            (pdf_filename, annotated_pdf, "markup_pdf"),
            (sheet_filename, sheet_pdf, "comment_sheet"),
        ):
            attachment_id = uuid.uuid4()
            key = build_key(
                project.code,
                "transmittals",
                str(session.transmittal_id),
                "comment-markups",
                f"{attachment_id}-{filename}",
            )
            storage.put_bytes(key, data, content_type="application/pdf")
            generated.append((attachment_id, key, data))
            attachments.append(
                _attachment_record(
                    attachment_id=attachment_id,
                    transmittal=session.transmittal,
                    line=line,
                    filename=filename,
                    content_type="application/pdf",
                    data=data,
                    user=user,
                    storage_key=key,
                    session_id=session.id,
                    related_type=related_type,
                )
            )

        comment_list = transmittal_service.ensure_comment_list(
            db, revision_id=session.revision_id, transmittal_id=session.transmittal_id
        )
        db.flush()
        comment_ids: list[str] = []
        comment_objects: list[Comment] = []
        for annotation in annotations:
            comment = Comment(
                comment_list_id=comment_list.id,
                revision_id=session.revision_id,
                sheet_id=None,
                author_id=user.id,
                seq_no=_next_comment_number(db, project.id, comment_list.id),
                code="2",
                category="PDF markup",
                text=annotation.text or f"PDF markup: {annotation.kind}",
                page_no=annotation.page_no,
                from_page_no=annotation.page_no,
                to_page_no=annotation.page_no,
                bbox=annotation.points or [],
                status=CommentStatus.OPEN,
                disposition=None,
                judgment="draft",
                personal_remarks=None,
                remarks=None,
            )
            db.add(comment)
            db.flush()
            comment_ids.append(str(comment.id))
            comment_objects.append(comment)
        if not comment_objects:
            # Keep generated files discoverable through the existing Comment
            # attachment viewer even when a user submits an empty sheet.
            comment = Comment(
                comment_list_id=comment_list.id,
                revision_id=session.revision_id,
                author_id=user.id,
                seq_no=_next_comment_number(db, project.id, comment_list.id),
                code="3",
                category="PDF markup",
                text="Comment Sheet generated without annotations",
                page_no=1,
                bbox=[],
                status=CommentStatus.OPEN,
                judgment="draft",
            )
            db.add(comment)
            db.flush()
            comment_ids.append(str(comment.id))
            comment_objects.append(comment)
        for attachment_id, key, data in generated:
            db.add(
                CommentAttachment(
                    comment_id=comment_objects[0].id,
                    file_name=next(item["filename"] for item in attachments if item["id"] == str(attachment_id)),
                    file_path=key,
                    file_size=len(data),
                    mime_type="application/pdf",
                    uploaded_by_id=user.id,
                )
            )

        metadata = dict(session.transmittal.meta or {})
        existing_attachments = list(metadata.get("attachments") or [])
        metadata["attachments"] = [*existing_attachments, *attachments]
        session.transmittal.meta = metadata
        session.status = "submitted"
        session.submitted_at = now
        session.submitted_pdf_attachment_id = attachments[0]["id"]
        session.comment_sheet_attachment_id = attachments[1]["id"]
        session.generated_comment_ids = comment_ids
        db.flush()
        audit.record(
            db,
            action="markup.submit",
            entity_type="markup_session",
            entity_id=session.id,
            actor=user,
            project_id=project.id,
            after={
                "annotation_count": len(annotations),
                "pdf_attachment_id": attachments[0]["id"],
                "comment_sheet_attachment_id": attachments[1]["id"],
                "original_sha256": session.original_sha256,
            },
        )
    except Exception:
        for _, key, _ in generated:
            try:
                storage.delete(key)
            except Exception:
                pass
        raise

    outputs = [
        MarkupOutputOut(
            attachment_id=uuid.UUID(item["id"]),
            filename=item["filename"],
            kind="pdf" if item["related_type"] == "markup_pdf" else "sheet",
            download_url=(
                f"{settings.api_v1_prefix}/transmittals/{session.transmittal_id}/attachments/"
                f"{item['id']}/download"
            ),
        )
        for item in attachments
    ]
    return MarkupSubmitOut(
        session=_session_out(db, session, user),
        outputs=outputs,
        annotation_count=len(annotations),
    )


def reply(db: Session, user: User, session: MarkupSession, number: int, payload: MarkupReply) -> MarkupAnnotation:
    if _status(session) != "submitted":
        raise HTTPException(status_code=409, detail="پاسخ فقط برای جلسه ثبت نهایی‌شده ممکن است.")
    _access(db, user, session.transmittal)
    if not _can_reply(db, user, session.transmittal):
        raise acl.AccessDenied("فقط مالک مدرک یا مسئول کنترل مجاز به پاسخ است.")
    annotation = db.execute(
        select(MarkupAnnotation).where(
            MarkupAnnotation.session_id == session.id,
            MarkupAnnotation.number == number,
        )
    ).scalar_one_or_none()
    if annotation is None:
        raise HTTPException(status_code=404, detail="نشانه‌گذاری مورد نظر یافت نشد.")
    now = datetime.now(timezone.utc)
    annotation.reply_status = payload.status
    annotation.reply_text = payload.text
    annotation.replied_by_id = user.id
    annotation.replied_by_name = user.full_name
    annotation.replied_at = now
    # Keep the existing Comment List projection synchronized without adding a
    # second reply table.  The generated comment IDs follow annotation.number.
    comment_id = None
    generated_ids = session.generated_comment_ids or []
    if 0 < number <= len(generated_ids):
        comment_id = _safe_uuid(str(generated_ids[number - 1]))
    comment = db.get(Comment, comment_id) if comment_id is not None else None
    if comment is not None:
        comment.response_text = payload.text
        comment.responded_by_id = user.id
        comment.responded_at = now
        if payload.status == "open":
            comment.status = CommentStatus.OPEN
        else:
            comment.status = CommentStatus.CLOSED
        if payload.status in {"accepted", "rejected"}:
            comment.disposition = payload.status
    db.flush()
    audit.record(
        db,
        action="markup.reply",
        entity_type="markup_annotation",
        entity_id=annotation.id,
        actor=user,
        project_id=session.project_id,
        after={"session_id": session.id, "number": number, "status": payload.status},
    )
    return annotation


def delete(db: Session, user: User, session: MarkupSession) -> None:
    if _status(session) != "draft":
        raise HTTPException(status_code=409, detail="جلسه ثبت نهایی‌شده قابل حذف نیست.")
    transmittal = session.transmittal
    project = acl.require_transmittal(db, user, transmittal)
    acl.assert_admin(user)
    storage = get_storage()
    output_ids = {session.submitted_pdf_attachment_id, session.comment_sheet_attachment_id}
    metadata = dict(transmittal.meta or {})
    kept: list[dict[str, Any]] = []
    for attachment in list(metadata.get("attachments") or []):
        if str(attachment.get("id")) in {str(item) for item in output_ids if item}:
            try:
                storage.delete(str(attachment.get("storage_key")))
            except Exception:
                pass
        else:
            kept.append(attachment)
    metadata["attachments"] = kept
    transmittal.meta = metadata
    for comment_id in session.generated_comment_ids or []:
        comment = db.get(Comment, _safe_uuid(str(comment_id))) if _safe_uuid(str(comment_id)) else None
        if comment is not None:
            db.delete(comment)
    audit.record(
        db,
        action="markup.delete",
        entity_type="markup_session",
        entity_id=session.id,
        actor=user,
        project_id=project.id,
        before={"status": _status(session), "generated_comment_ids": session.generated_comment_ids or []},
    )
    db.delete(session)
