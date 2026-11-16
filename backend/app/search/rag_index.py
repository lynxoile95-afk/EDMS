"""Resumable/throttled indexing for the additive Persian RAG table."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import DocumentRevision, DocumentChunkV2, RAGIndexJob, SourceFile
from app.search.embeddings import EmbeddingUnavailable, get_rag_embedding_service
from app.search.extraction import extract_docx_blocks, extract_pdf_pages
from app.search.rag_chunking import Chunk, chunk_blocks
from app.services.storage import get_storage

logger = logging.getLogger(__name__)
RAG_INDEX_VERSION = "persian-rag-v2"
ACTIVE_RAG_JOB_STATUSES = ("queued", "running")


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


def create_rag_index_jobs(
    db: Session,
    file_ids: list[uuid.UUID],
    *,
    requested_by_id: uuid.UUID | None = None,
    force: bool = False,
) -> list[RAGIndexJob]:
    """Create at most one active v2 job per file, without touching old chunks."""

    jobs: list[RAGIndexJob] = []
    for file_id in file_ids:
        source_file = db.get(SourceFile, file_id)
        if source_file is None:
            continue
        current = db.execute(
            select(RAGIndexJob)
            .where(
                RAGIndexJob.file_id == file_id,
                RAGIndexJob.status.in_(ACTIVE_RAG_JOB_STATUSES),
            )
            .order_by(RAGIndexJob.created_at.desc())
            .limit(1)
        ).scalars().first()
        if current is not None:
            jobs.append(current)
            continue
        if not force:
            completed = db.execute(
                select(RAGIndexJob)
                .where(RAGIndexJob.file_id == file_id, RAGIndexJob.status == "completed")
                .order_by(RAGIndexJob.created_at.desc())
                .limit(1)
            ).scalars().first()
            if completed is not None:
                jobs.append(completed)
                continue
        revision = _current_revision(db, file_id)
        job = RAGIndexJob(
            file_id=file_id,
            project_id=source_file.project_id,
            revision_id=revision.id if revision else None,
            index_version=RAG_INDEX_VERSION,
            model_name=settings.file_qa_embedding_model,
            # The v2 contract is intentionally fixed; this must never drift from
            # the vector(384) migration even if an operator has a stale env value.
            dimensions=384,
            requested_by_id=requested_by_id,
            force_reindex=force,
            status="queued",
        )
        db.add(job)
        db.flush()
        jobs.append(job)
    return jobs


def enqueue_rag_index_jobs(
    db: Session,
    file_ids: list[uuid.UUID],
    *,
    requested_by_id: uuid.UUID | None = None,
    force: bool = False,
) -> list[RAGIndexJob]:
    jobs = create_rag_index_jobs(
        db, file_ids, requested_by_id=requested_by_id, force=force
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Could not persist Persian RAG index jobs")
        return []

    try:
        from app.workers.rag_tasks import index_rag_file

        for job in jobs:
            if job.status in ACTIVE_RAG_JOB_STATUSES:
                try:
                    index_rag_file.delay(str(job.id))
                except Exception:  # noqa: BLE001 - the durable row is resumable
                    logger.warning("Could not enqueue Persian RAG job %s", job.id)
    except Exception:  # noqa: BLE001 - optional queue is not an HTTP dependency
        logger.warning("Persian RAG task registration is unavailable")
    return jobs


def _source_blocks(path: Path, source_file: SourceFile) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_docx_blocks(path)
    if suffix == ".pdf":
        pages = extract_pdf_pages(
            str(path),
            # This is the additive index path; existing PDF/OCR extraction remains
            # unchanged and is not called when the feature flag is off.
            ocr_enabled=settings.search_ocr_enabled,
            ocr_langs=settings.ocr_langs or "eng+fas",
            ocr_dpi=settings.ocr_dpi,
            max_pixels=settings.ocr_max_pixels,
        )
        return [
            (f"page:{page.page_no}", page.text)
            for page in pages
            if page.text and not page.error
        ]
    raise ValueError("فقط فایل‌های DOCX و PDF برای فهرست RAG پشتیبانی می‌شوند.")


def _load_chunks(source_file: SourceFile) -> list[Chunk]:
    local_path = os.path.join(
        tempfile.gettempdir(),
        f"edms-rag-{source_file.id}{Path(source_file.original_filename).suffix.lower() or '.bin'}",
    )
    get_storage().download_to(source_file.storage_key, local_path)
    try:
        blocks = _source_blocks(Path(local_path), source_file)
        return chunk_blocks(
            blocks,
            max_words=220,
            overlap_words=45,
            document_name=source_file.original_filename,
            document_path=source_file.storage_key,
        )
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


def _upsert_chunk(
    db: Session,
    *,
    job: RAGIndexJob,
    source_file: SourceFile,
    revision_id: uuid.UUID | None,
    item: Chunk,
    embedding: list[float] | None,
) -> DocumentChunkV2:
    chunk_index = int(item.id.rsplit("-", 1)[-1]) - 1
    revision_clause = (
        DocumentChunkV2.revision_id.is_(None)
        if revision_id is None
        else DocumentChunkV2.revision_id == revision_id
    )
    existing = db.execute(
        select(DocumentChunkV2).where(
            DocumentChunkV2.file_id == source_file.id,
            revision_clause,
            DocumentChunkV2.page_no == item.page_no,
            DocumentChunkV2.chunk_index == chunk_index,
            DocumentChunkV2.index_version == job.index_version,
        )
    ).scalars().first()
    if existing is None:
        existing = DocumentChunkV2(
            file_id=source_file.id,
            project_id=source_file.project_id,
            revision_id=revision_id,
            index_version=job.index_version,
            chunk_index=chunk_index,
            page_no=item.page_no,
            text_original=item.text,
            text_normalized=item.normalized_text,
            content_hash=hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            source=item.source,
            source_document=item.source_document,
            source_path=item.source_path,
            embedding=embedding,
            embedding_model=job.model_name,
            embedding_dimensions=job.dimensions,
            is_active=False,
        )
        db.add(existing)
    else:
        existing.text_original = item.text
        existing.text_normalized = item.normalized_text
        existing.content_hash = hashlib.sha256(item.text.encode("utf-8")).hexdigest()
        existing.source = item.source
        existing.source_document = item.source_document
        existing.source_path = item.source_path
        existing.embedding = embedding
        existing.embedding_model = job.model_name
        existing.embedding_dimensions = job.dimensions
        existing.is_active = False
    return existing


def _run_job_in_session(db: Session, job: RAGIndexJob) -> dict:
    # SQLite Preview always permits the additive plain-text index. PostgreSQL
    # keeps the existing FILE_QA_ENABLED gate for advanced RAG workers.
    if not settings.file_qa_enabled and db.get_bind().dialect.name == "postgresql":
        job.status = "disabled"
        job.error_message = "FILE_QA_ENABLED=false"
        db.commit()
        return {"status": "disabled", "job_id": str(job.id)}
    source_file = db.get(SourceFile, job.file_id)
    if source_file is None:
        job.status = "failed"
        job.error_message = "فایل مبدأ یافت نشد."
        db.commit()
        return {"status": "failed", "job_id": str(job.id)}

    current = _current_revision(db, source_file.id)
    current_id = current.id if current else None
    if current_id != job.revision_id:
        job.status = "superseded"
        job.error_message = "بازنگری جاری هنگام فهرست‌سازی تغییر کرد."
        db.commit()
        return {"status": "superseded", "job_id": str(job.id)}

    job.status = "running"
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.started_at = job.started_at or _now()
    job.heartbeat_at = _now()
    db.commit()

    chunks = _load_chunks(source_file)
    job.total_chunks = len(chunks)
    db.commit()
    if not chunks:
        job.status = "completed"
        job.completed_at = _now()
        job.heartbeat_at = _now()
        db.commit()
        return {"status": "completed", "job_id": str(job.id), "chunks": 0, "semantic": False}

    embeddings: list[list[float] | None] = [None] * len(chunks)
    semantic = False
    if db.get_bind().dialect.name == "postgresql":
        try:
            embeddings = get_rag_embedding_service().embed_passages(
                [item.normalized_text for item in chunks]
            )
            semantic = True
        except EmbeddingUnavailable as exc:
            # Keyword/LIKE retrieval still works; do not fail a valid document
            # merely because a large optional model is unavailable.
            job.error_message = str(exc)[:300]

    # Replace visibility with an update only. Existing rows and legacy 768 vectors
    # remain physically present for rollback/audit and are never deleted.
    same_revision = (
        DocumentChunkV2.revision_id == current_id
        if current_id is not None
        else DocumentChunkV2.revision_id.is_(None)
    )
    db.execute(
        update(DocumentChunkV2)
        .where(
            DocumentChunkV2.file_id == source_file.id,
            DocumentChunkV2.index_version == job.index_version,
            same_revision,
        )
        .values(is_active=False)
    )
    db.commit()

    start = max(0, int(job.cursor_chunk or 0))
    for index, item in enumerate(chunks):
        if index < start:
            continue
        _upsert_chunk(
            db,
            job=job,
            source_file=source_file,
            revision_id=job.revision_id,
            item=item,
            embedding=embeddings[index],
        )
        job.cursor_chunk = index + 1
        job.heartbeat_at = _now()
        db.commit()
        if settings.file_qa_reindex_throttle_ms > 0 and index + 1 < len(chunks):
            time.sleep(settings.file_qa_reindex_throttle_ms / 1000.0)

    visible_keys = or_(
        *[
            and_(DocumentChunkV2.page_no == item.page_no, DocumentChunkV2.chunk_index == index)
            for index, item in enumerate(chunks)
        ]
    )
    db.execute(
        update(DocumentChunkV2)
        .where(
            DocumentChunkV2.file_id == source_file.id,
            DocumentChunkV2.index_version == job.index_version,
            same_revision,
            visible_keys,
        )
        .values(is_active=True)
    )
    job.status = "completed"
    job.completed_at = _now()
    job.heartbeat_at = _now()
    db.commit()
    return {
        "status": "completed",
        "job_id": str(job.id),
        "chunks": len(chunks),
        "semantic": semantic,
    }


def run_rag_index_job(job_id: str | uuid.UUID) -> dict:
    """Run or resume a v2 job with page/chunk-boundary commits."""

    parsed = uuid.UUID(str(job_id))
    with SessionLocal() as db:
        job = db.get(RAGIndexJob, parsed)
        if job is None:
            return {"status": "not_found", "job_id": str(parsed)}
        if job.status == "completed":
            return {"status": "completed", "job_id": str(parsed)}
        try:
            return _run_job_in_session(db, job)
        except Exception:  # noqa: BLE001 - preserve resumable row, never log content
            db.rollback()
            job = db.get(RAGIndexJob, parsed)
            if job is not None:
                job.status = "failed"
                job.error_message = "فهرست‌سازی فارسی فایل ناموفق بود."
                job.heartbeat_at = _now()
                db.commit()
            logger.warning("Persian RAG job %s failed", parsed)
            return {"status": "failed", "job_id": str(parsed)}


def build_rag_index_file(db: Session, file_id: uuid.UUID, *, force: bool = True) -> dict:
    """Small synchronous helper for tests and administrator-controlled rebuilds."""

    jobs = create_rag_index_jobs(db, [file_id], requested_by_id=None, force=force)
    if not jobs:
        return {"status": "not_found", "file_id": str(file_id)}
    db.commit()
    # Use the request session for predictable SQLite tests; the same code path is
    # still resumable when invoked by the worker.
    job = db.get(RAGIndexJob, jobs[0].id)
    assert job is not None
    return _run_job_in_session(db, job)


__all__ = [
    "RAG_INDEX_VERSION",
    "create_rag_index_jobs",
    "enqueue_rag_index_jobs",
    "run_rag_index_job",
    "build_rag_index_file",
]
