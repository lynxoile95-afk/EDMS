"""Durable PDF indexing orchestration.

Indexing is deliberately separate from the existing extraction pipeline. It
stages a complete version of a file's chunks, then flips ``is_active`` in one
transaction so searches never observe a half-built revision.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import DocumentChunk, DocumentRevision, IndexJob, SourceFile
from app.search.chunking import PageText, chunk_pages
from app.search.embeddings import EmbeddingUnavailable, get_embedding_service
from app.search.extraction import ExtractedPage, extract_docx_blocks, extract_pdf_pages
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = ("queued", "running")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _current_revision(db: Session, file_id: uuid.UUID) -> DocumentRevision | None:
    return db.execute(
        select(DocumentRevision)
        .where(
            DocumentRevision.source_file_id == file_id,
            DocumentRevision.is_current.is_(True),
        )
        .order_by(DocumentRevision.rev_seq.desc(), DocumentRevision.registered_at.desc())
        .limit(1)
    ).scalars().first()


def create_index_jobs(
    db: Session,
    file_ids: list[uuid.UUID],
    *,
    requested_by_id: uuid.UUID | None = None,
    force: bool = False,
) -> list[IndexJob]:
    """Create at most one queued/running job per file unless force is requested."""

    jobs: list[IndexJob] = []
    for file_id in file_ids:
        source_file = db.get(SourceFile, file_id)
        if source_file is None:
            continue
        current = db.execute(
            select(IndexJob)
            .where(
                IndexJob.file_id == file_id,
                IndexJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(IndexJob.created_at.desc())
            .limit(1)
        ).scalars().first()
        if current is not None:
            jobs.append(current)
            continue
        if not force:
            completed = db.execute(
                select(IndexJob)
                .where(IndexJob.file_id == file_id, IndexJob.status == "completed")
                .order_by(IndexJob.created_at.desc())
                .limit(1)
            ).scalars().first()
            if completed is not None:
                # Upload hooks are idempotent. Backfill can explicitly force a
                # fresh version when an administrator needs it.
                jobs.append(completed)
                continue
        revision = _current_revision(db, file_id)
        job = IndexJob(
            file_id=file_id,
            project_id=source_file.project_id,
            revision_id=revision.id if revision else None,
            index_version=uuid.uuid4().hex,
            status="queued",
            requested_by_id=requested_by_id,
            force_reindex=force,
        )
        db.add(job)
        db.flush()
        jobs.append(job)
    return jobs


def enqueue_index_jobs(
    db: Session,
    file_ids: list[uuid.UUID],
    *,
    requested_by_id: uuid.UUID | None = None,
    force: bool = False,
) -> list[IndexJob]:
    """Persist jobs and best-effort enqueue them without affecting upload success."""

    if not settings.search_indexing_enabled:
        return []
    jobs = create_index_jobs(db, file_ids, requested_by_id=requested_by_id, force=force)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Could not persist content-index jobs")
        return []

    try:
        from app.workers.search_tasks import index_file

        # Celery's eager mode is a test/development convenience that would make
        # an HTTP upload execute OCR/embeddings inline. Leave the durable row
        # queued in that mode so the upload contract remains non-blocking.
        if settings.celery_task_always_eager:
            return jobs
        for job in jobs:
            if job.status in ACTIVE_JOB_STATUSES:
                try:
                    index_file.delay(str(job.id))
                except Exception:  # noqa: BLE001 - resumable row remains queued
                    logger.warning("Could not enqueue content-index job %s", job.id)
    except Exception:  # noqa: BLE001 - optional queue is not an upload dependency
        logger.warning("Content-index task registration is unavailable")
    return jobs


def _mark_failed(job_id: uuid.UUID, message: str) -> None:
    with SessionLocal() as db:
        job = db.get(IndexJob, job_id)
        if job is None:
            return
        job.status = "failed"
        job.error_message = message[:500]
        job.heartbeat_at = _now()
        db.commit()


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _insert_page_chunks(
    db: Session,
    *,
    job: IndexJob,
    source_file: SourceFile,
    page: PageText,
    chunks,
    embeddings: list[list[float] | None],
    revision_id: uuid.UUID | None,
) -> None:
    for item, embedding in zip(chunks, embeddings, strict=True):
        db.add(
            DocumentChunk(
                file_id=source_file.id,
                project_id=source_file.project_id,
                revision_id=revision_id,
                index_version=job.index_version,
                page_no=item.page_no,
                chunk_index=item.chunk_index,
                content=item.content,
                content_hash=hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                source_type=item.source_type,
                char_start=item.char_start,
                char_end=item.char_end,
                ocr_confidence=item.ocr_confidence,
                embedding=embedding,
                is_active=False,
            )
        )


def run_index_job(job_id: str | uuid.UUID) -> dict:
    """Run/resume one job. Database commits occur at page boundaries."""

    parsed_job_id = uuid.UUID(str(job_id))
    db = SessionLocal()
    local_path: str | None = None
    try:
        job = db.get(IndexJob, parsed_job_id)
        if job is None:
            return {"status": "not_found", "job_id": str(parsed_job_id)}
        if not settings.search_indexing_enabled:
            job.status = "disabled"
            job.error_message = "SEARCH_INDEXING_ENABLED=false"
            db.commit()
            return {"status": "disabled", "job_id": str(parsed_job_id)}
        if job.status == "completed":
            return {"status": "completed", "job_id": str(parsed_job_id)}

        source_file = db.get(SourceFile, job.file_id)
        if source_file is None:
            job.status = "failed"
            job.error_message = "فایل مبدأ یافت نشد."
            db.commit()
            return {"status": "failed", "job_id": str(parsed_job_id)}

        job.status = "running"
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.started_at = job.started_at or _now()
        job.heartbeat_at = _now()
        db.commit()

        suffix = os.path.splitext(source_file.original_filename)[1].lower()
        local_path = os.path.join(
            tempfile.gettempdir(), f"edms-index-{source_file.id}{suffix or '.pdf'}"
        )
        get_storage().download_to(source_file.storage_key, local_path)
        if suffix == ".docx":
            blocks = extract_docx_blocks(local_path)
            pages = [
                ExtractedPage(1, "\n".join(text for _, text in blocks), "native")
            ]
        else:
            pages = extract_pdf_pages(
                local_path,
                # The legacy PDF/OCR behavior remains unchanged.
                ocr_enabled=settings.search_ocr_enabled,
                ocr_langs="eng+fas",
                ocr_dpi=settings.ocr_dpi,
                max_pixels=settings.ocr_max_pixels,
            )
        job.total_pages = len(pages)
        db.commit()

        postgres = _is_postgres(db)
        semantic_error: str | None = None
        for extracted in pages:
            if extracted.page_no <= int(job.cursor_page or 0):
                continue
            if extracted.error:
                failed = list(job.failed_pages or [])
                failed.append({"page": extracted.page_no, "message": extracted.error[:300]})
                job.failed_pages = failed
                job.cursor_page = extracted.page_no
                job.heartbeat_at = _now()
                db.commit()
                continue

            page = PageText(
                page_no=extracted.page_no,
                text=extracted.text,
                source_type=extracted.source_type,
                ocr_confidence=extracted.ocr_confidence,
            )
            chunks = chunk_pages([page], max_chars=800, overlap=100)
            embeddings: list[list[float] | None] = [None] * len(chunks)
            if chunks and postgres:
                try:
                    embeddings = get_embedding_service().embed_passages(
                        [chunk.content for chunk in chunks]
                    )
                except EmbeddingUnavailable as exc:
                    # Preserve a useful keyword index if the optional model is
                    # unavailable. The status warning is exposed to admins; no
                    # model exception or document content is logged.
                    semantic_error = str(exc)[:300]
                    embeddings = [None] * len(chunks)

            if chunks:
                _insert_page_chunks(
                    db,
                    job=job,
                    source_file=source_file,
                    page=page,
                    chunks=chunks,
                    embeddings=embeddings,
                    revision_id=job.revision_id,
                )
            job.cursor_page = extracted.page_no
            job.heartbeat_at = _now()
            if semantic_error:
                job.error_message = semantic_error
            db.commit()

        # A revision can be registered while a long PDF is indexing. Do not make
        # the older staged version current; an admin/backfill can enqueue the new
        # revision, preserving the atomic latest-revision rule.
        current = _current_revision(db, source_file.id)
        current_id = current.id if current else None
        if current_id != job.revision_id:
            job.status = "superseded"
            job.error_message = "بازنگری جاری هنگام فهرست‌سازی تغییر کرد."
            job.heartbeat_at = _now()
            db.commit()
            return {"status": "superseded", "job_id": str(parsed_job_id)}

        # One transaction is the visibility boundary: old active chunks disappear
        # and this complete staged version becomes visible together.
        # Keep chunks belonging to an older registered revision physically
        # searchable for an explicit audit query; the default query excludes
        # them through current_revision_clause(). Chunks for the same revision
        # (or unregistered files) are replaced in place.
        same_revision = (
            DocumentChunk.revision_id == current_id
            if current_id is not None
            else DocumentChunk.revision_id.is_(None)
        )
        db.execute(
            update(DocumentChunk)
            .where(
                DocumentChunk.file_id == source_file.id,
                DocumentChunk.is_active.is_(True),
                same_revision,
            )
            .values(is_active=False)
        )
        db.execute(
            update(DocumentChunk)
            .where(
                DocumentChunk.file_id == source_file.id,
                DocumentChunk.index_version == job.index_version,
            )
            .values(is_active=True)
        )
        job.status = "completed"
        job.completed_at = _now()
        job.heartbeat_at = _now()
        db.commit()
        return {
            "status": "completed",
            "job_id": str(parsed_job_id),
            "pages": len(pages),
            "failed_pages": len(job.failed_pages or []),
            "semantic": postgres and semantic_error is None,
        }
    except Exception as exc:  # noqa: BLE001 - task wrapper isolates one file
        db.rollback()
        _mark_failed(parsed_job_id, "فهرست‌سازی فایل ناموفق بود.")
        logger.warning("Content-index job %s failed: %s", parsed_job_id, type(exc).__name__)
        return {"status": "failed", "job_id": str(parsed_job_id)}
    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.unlink(local_path)
            except OSError:
                pass
        db.close()
