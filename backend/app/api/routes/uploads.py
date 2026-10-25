"""Upload session endpoints (drag & drop -> commit -> processing)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.core.config import settings
from app.models import ExtractionResult, ProjectMember, SourceFile, UploadSession
from app.models.enums import ProjectRole, UploadStatus
from app.schemas.uploads import (
    CommitResponse,
    SourceFileOut,
    UploadSessionCreate,
    UploadSessionDetail,
    UploadSessionOut,
)
from app.services import acl, ingest
from app.services.ingest import UploadRejected

router = APIRouter(tags=["uploads"])


@router.post(
    "/projects/{project_id}/upload-sessions",
    response_model=UploadSessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="ایجاد جلسه بارگذاری",
)
def create_session(
    project: ProjectDep, payload: UploadSessionCreate, db: DbSession, user: CurrentUser
) -> UploadSessionOut:
    acl.require_project(db, user, project.id)
    session = ingest.create_session(
        db,
        project=project,
        user=user,
        purpose=payload.purpose,
        mode=payload.mode,
        transmittal_type=payload.transmittal_type,
        note=payload.note,
    )
    db.commit()
    return UploadSessionOut.model_validate(session)


@router.post(
    "/upload-sessions/{session_id}/files",
    response_model=list[SourceFileOut],
    summary="بارگذاری یک یا چند فایل در جلسه",
)
async def upload_files(
    session_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    files: list[UploadFile] = File(...),
) -> list[SourceFileOut]:
    session = db.get(UploadSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="جلسه بارگذاری یافت نشد.")
    project = acl.require_project(db, user, session.project_id)
    if session.status in {UploadStatus.COMPLETED, UploadStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="این جلسه بسته شده است.")
    if len(files) > 50:
        raise HTTPException(status_code=422, detail="حداکثر ۵۰ فایل در هر درخواست.")

    stored = []
    duplicates = []
    for upload in files:
        try:
            outcome = ingest.store_file(
                db, session=session, upload=upload, user=user, project_code=project.code
            )
        except UploadRejected as exc:
            db.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            raise HTTPException(status_code=500, detail=f"ذخیره فایل ناموفق بود: {exc}") from exc
        if outcome.duplicate:
            duplicates.append(outcome.source_file.original_filename)
        stored.append(outcome.source_file)

    db.commit()
    return [SourceFileOut.model_validate(row) for row in stored]


@router.get("/upload-sessions/{session_id}", response_model=UploadSessionDetail, summary="وضعیت جلسه")
def get_session(session_id: uuid.UUID, db: DbSession, user: CurrentUser) -> UploadSessionDetail:
    session = db.get(UploadSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="جلسه یافت نشد.")
    acl.require_project(db, user, session.project_id)
    results = db.execute(
        select(ExtractionResult).where(ExtractionResult.session_id == session.id)
    ).scalars()
    detail = UploadSessionDetail.model_validate(session)
    detail.results = [
        {
            "id": str(r.id),
            "doc_code": r.doc_code,
            "rev": r.rev,
            "title": r.title,
            "decision": str(r.decision),
            "confidence": r.overall_confidence,
            "review_status": str(r.review_status),
        }
        for r in results
    ]
    return detail


@router.post(
    "/upload-sessions/{session_id}/commit",
    response_model=CommitResponse,
    summary="ثبت جلسه و شروع پردازش هوشمند",
)
def commit_session(session_id: uuid.UUID, db: DbSession, user: CurrentUser) -> CommitResponse:
    session = db.get(UploadSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="جلسه یافت نشد.")
    acl.require_project(db, user, session.project_id)
    try:
        ingest.commit_session(db, session=session, user=user)
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    db.commit()

    task_id = None
    try:
        from app.workers.tasks import process_upload_session

        async_result = process_upload_session.delay(str(session.id))
        task_id = getattr(async_result, "id", None)
    except Exception:  # noqa: BLE001 - queue unavailable: fall back to sync
        from app.db.session import SessionLocal
        from app.services.processing import process_session

        with SessionLocal() as sync_db:
            process_session(sync_db, session.id)
            sync_db.commit()

    return CommitResponse(
        session_id=session.id,
        status="processing",
        queued_files=len(session.files),
        task_id=task_id,
        message="پردازش آغاز شد؛ نتیجه در صف بازبینی یا فهرست مدارک نمایش داده می‌شود.",
    )


@router.post(
    "/upload-sessions/{session_id}/cancel",
    status_code=204,
    response_model=None,
    summary="لغو جلسه",
)
def cancel_session(session_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    session = db.get(UploadSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="جلسه یافت نشد.")
    acl.require_project(db, user, session.project_id)
    try:
        ingest.cancel_session(db, session=session, user=user)
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    db.commit()


@router.get("/projects/{project_id}/upload-sessions", response_model=list[UploadSessionOut])
def list_sessions(project: ProjectDep, db: DbSession, limit: int = 20) -> list[UploadSessionOut]:
    rows = db.execute(
        select(UploadSession)
        .where(UploadSession.project_id == project.id)
        .order_by(UploadSession.created_at.desc())
        .limit(min(limit, 100))
    ).scalars()
    return [UploadSessionOut.model_validate(r) for r in rows]
