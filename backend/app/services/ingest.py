"""Upload session handling: validation, hashing, AV scan and file storage."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Project, SourceFile, UploadSession, User
from app.models.enums import AVStatus, FileStatus, UploadStatus
from app.services import audit
from app.services.av_scan import scan_bytes
from app.services.storage import build_key, get_storage

logger = logging.getLogger(__name__)

#: PDF magic bytes — extension is never trusted (NFR/ING-002)
_PDF_MAGIC = b"%PDF-"
_ALLOWED_SNIFF = {b"%PDF-": "application/pdf"}


class UploadRejected(HTTPException):
    def __init__(self, detail: str, code: str = "upload_rejected") -> None:
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
        self.code = code


@dataclass(slots=True)
class StoredFile:
    source_file: SourceFile
    duplicate: bool = False


def create_session(
    db: Session,
    *,
    project: Project,
    user: User,
    purpose: str,
    mode: str,
    transmittal_type: str,
    note: str | None,
) -> UploadSession:
    session = UploadSession(
        project_id=project.id,
        created_by_id=user.id,
        purpose=purpose,
        mode=mode,
        transmittal_type=transmittal_type,
        note=note,
        status=UploadStatus.CREATED,
        stats={"files": 0, "bytes": 0},
    )
    db.add(session)
    audit.record(
        db, action="upload.session.create", entity_type="upload_session", entity_id=session.id,
        actor=user, project_id=project.id, after={"purpose": purpose, "mode": mode},
    )
    db.flush()
    return session


def _sniff(head: bytes) -> str:
    for magic, mime in _ALLOWED_SNIFF.items():
        if head.startswith(magic):
            return mime
    raise UploadRejected("نوع فایل شناسایی نشد یا مجاز نیست؛ فقط PDF پذیرفته می‌شود.", "invalid_type")


def store_file(
    db: Session,
    *,
    session: UploadSession,
    upload: UploadFile,
    user: User,
    project_code: str,
) -> StoredFile:
    """Stream a multipart upload to storage, with hash, size and AV checks."""
    max_bytes = settings.max_upload_mb * 1024 * 1024
    filename = (upload.filename or "document.pdf").strip()[:500]
    storage = get_storage()

    digest = hashlib.sha256()
    size = 0
    head = b""
    tmp_path = os.path.join(tempfile.gettempdir(), f"edms-{uuid.uuid4().hex}.pdf")
    try:
        with open(tmp_path, "wb") as handle:
            while chunk := upload.file.read(1024 * 1024):
                if not head:
                    head = chunk[:8]
                    mime = _sniff(head)
                size += len(chunk)
                if size > max_bytes:
                    raise UploadRejected(
                        f"حجم فایل بیشتر از حد مجاز ({settings.max_upload_mb} مگابایت) است.", "too_large"
                    )
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise UploadRejected("فایل خالی است.", "empty_file")
        sha = digest.hexdigest()

        # duplicate check (same physical file in this project)
        existing = db.execute(
            select(SourceFile).where(SourceFile.project_id == session.project_id, SourceFile.sha256 == sha)
        ).scalar_one_or_none()
        if existing is not None:
            return StoredFile(source_file=existing, duplicate=True)

        scan = scan_bytes(open(tmp_path, "rb").read())
        if scan.status is AVStatus.INFECTED:
            raise UploadRejected(
                f"فایل آلوده به بدافزار شناسایی شد ({scan.signature}).", "malware_detected"
            )

        key = build_key(
            project_code,
            "incoming",
            datetime.now(timezone.utc).strftime("%Y/%m"),
            str(session.id),
            f"{uuid.uuid4().hex}.pdf",
        )
        storage.put_file(key, tmp_path, content_type="application/pdf")

        source_file = SourceFile(
            session_id=session.id,
            project_id=session.project_id,
            original_filename=filename,
            storage_key=key,
            mime_type=mime,
            size_bytes=size,
            sha256=sha,
            av_status=scan.status,
            status=FileStatus.UPLOADED,
            uploaded_by_id=user.id,
        )
        db.add(source_file)
        db.flush()

        stats = dict(session.stats or {})
        stats["files"] = int(stats.get("files", 0)) + 1
        stats["bytes"] = int(stats.get("bytes", 0)) + size
        session.stats = stats
        session.status = UploadStatus.UPLOADING

        audit.record(
            db, action="upload.file.store", entity_type="source_file", entity_id=source_file.id,
            actor=user, project_id=session.project_id,
            after={"filename": filename, "size": size, "sha256": sha, "av": str(scan.status)},
        )
        return StoredFile(source_file=source_file)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def commit_session(db: Session, *, session: UploadSession, user: User) -> UploadSession:
    if not session.files:
        raise UploadRejected("هیچ فایلی برای پردازش وجود ندارد.", "empty_session")
    if session.status in {UploadStatus.COMPLETED, UploadStatus.CANCELLED}:
        raise UploadRejected("این جلسه بارگذاری قابل پردازش مجدد نیست.", "session_closed")
    session.status = UploadStatus.PROCESSING
    audit.record(
        db, action="upload.session.commit", entity_type="upload_session", entity_id=session.id,
        actor=user, project_id=session.project_id, after={"files": len(session.files)},
    )
    db.flush()
    return session


def cancel_session(db: Session, *, session: UploadSession, user: User) -> UploadSession:
    if session.status is UploadStatus.COMPLETED:
        raise UploadRejected("جلسه پردازش‌شده قابل لغو نیست.", "session_completed")
    session.status = UploadStatus.CANCELLED
    for source_file in session.files:
        if source_file.status is FileStatus.UPLOADED:
            source_file.status = FileStatus.REJECTED
            source_file.error_message = "جلسه بارگذاری توسط کاربر لغو شد."
    audit.record(
        db, action="upload.session.cancel", entity_type="upload_session", entity_id=session.id,
        actor=user, project_id=session.project_id,
    )
    db.flush()
    return session
