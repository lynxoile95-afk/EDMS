"""Authenticated downloads via short-lived signed URLs."""

from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.models import DocumentRevision, ExtractionResult, MDLItem, Sheet, SourceFile
from app.services import acl, audit
from app.services.storage import get_storage

router = APIRouter(prefix="/files", tags=["files"])


def _signed(key: str, filename: str | None = None, ttl: int | None = None) -> str:
    return get_storage().presigned_url(key, expires_seconds=ttl, filename=filename)


@router.get("/download/{key:path}", summary="دریافت فایل از انبار محلی (محیط توسعه)")
def download_local_object(key: str, db: DbSession, user: CurrentUser) -> Response:
    """Local-storage counterpart of an S3 pre-signed URL.

    ``LocalStorage.presigned_url()`` points here, so every redirect-based download
    (source PDF, revision, cover sheet) works without MinIO. The route is
    authenticated and only reachable while ``STORAGE_BACKEND=local``.
    """
    if settings.storage_backend != "local":
        raise HTTPException(status_code=404, detail="این مسیر فقط با ذخیره‌سازی محلی فعال است.")
    storage = get_storage()
    if not storage.exists(key):
        raise HTTPException(status_code=404, detail="فایل یافت نشد.")
    media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    filename = key.rsplit("/", 1)[-1]
    return Response(
        content=storage.get_bytes(key),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/revisions/{revision_id}", summary="دانلود فایل یک بازنگری")
def download_revision(
    revision_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    ttl: int = Query(900, ge=60, le=7200),
):
    revision = db.get(DocumentRevision, revision_id)
    if revision is None or revision.source_file_id is None:
        raise HTTPException(status_code=404, detail="فایل بازنگری یافت نشد.")
    acl.require_project(db, user, revision.project_id)
    source_file = db.get(SourceFile, revision.source_file_id)
    if source_file is None:
        raise HTTPException(status_code=404, detail="فایل مبدأ یافت نشد.")
    audit.record(
        db, action="file.download.revision", entity_type="document_revision", entity_id=revision.id,
        actor=user, project_id=revision.project_id,
    )
    db.commit()
    return RedirectResponse(
        url=_signed(source_file.storage_key, filename=source_file.original_filename, ttl=ttl),
        status_code=302,
    )


@router.get("/source-files/{file_id}", summary="دانلود فایل اصلی بارگذاری‌شده")
def download_source_file(file_id: uuid.UUID, db: DbSession, user: CurrentUser):
    source_file = db.get(SourceFile, file_id)
    if source_file is None:
        raise HTTPException(status_code=404, detail="فایل یافت نشد.")
    acl.require_project(db, user, source_file.project_id)
    audit.record(
        db, action="file.download.source", entity_type="source_file", entity_id=source_file.id,
        actor=user, project_id=source_file.project_id,
    )
    db.commit()
    return RedirectResponse(
        url=_signed(source_file.storage_key, filename=source_file.original_filename), status_code=302
    )


@router.get("/sheets/{sheet_id}/thumbnail", summary="تصویر بندانگشتی صفحه")
def sheet_thumbnail(sheet_id: uuid.UUID, db: DbSession, user: CurrentUser):
    sheet = db.get(Sheet, sheet_id)
    if sheet is None or not sheet.thumbnail_key:
        raise HTTPException(status_code=404, detail="تصویر یافت نشد.")
    acl.require_project(db, user, sheet.project_id)
    return RedirectResponse(url=_signed(sheet.thumbnail_key), status_code=302)


@router.get("/sheets/{sheet_id}/title-block", summary="برش کادر مشخصات (Title Block)")
def sheet_title_block(sheet_id: uuid.UUID, db: DbSession, user: CurrentUser):
    sheet = db.get(Sheet, sheet_id)
    if sheet is None or not sheet.title_block_key:
        raise HTTPException(status_code=404, detail="تصویر یافت نشد.")
    acl.require_project(db, user, sheet.project_id)
    return RedirectResponse(url=_signed(sheet.title_block_key), status_code=302)


@router.get("/results/{result_id}/raw-text", summary="متن خام استخراج‌شده (ممیزی)")
def raw_text(result_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    result = db.get(ExtractionResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="نتیجه یافت نشد.")
    acl.require_project(db, user, result.project_id)
    return Response(
        content=result.raw_text or "",
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="raw-{result_id}.txt"'},
    )
