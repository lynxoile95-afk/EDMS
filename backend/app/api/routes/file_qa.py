"""Authenticated, feature-gated Persian file-QA endpoints."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import and_, exists, func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.models import (
    DocumentChunkV2,
    FileQAMessage,
    FileQASession,
    RAGIndexJob,
    SourceFile,
)
from app.schemas.file_qa import (
    FileQAAskRequest,
    FileQAAskResponse,
    FileQAClearRequest,
    FileQADocumentOut,
    FileQAHistoryOut,
    FileQAMessageOut,
    FileQASourceOut,
    FileQAStatusOut,
    FileQARebuildRequest,
    RAGIndexJobOut,
)
from app.search.file_qa import FileQAUnavailable, NO_ANSWER, answer_from_hits
from app.search.rag_chunking import chunk_blocks
from app.search.rag_index import enqueue_rag_index_jobs
from app.search.rag_query import retrieve_rag_chunks, retrieve_temporary_chunks
from app.search.extraction import extract_document_blocks
from app.services import acl

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/file-qa", tags=["file-qa"])

_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_WINDOW = 60.0


def _rate_limit(user_id: uuid.UUID) -> None:
    now = time.monotonic()
    key = str(user_id)
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS[key]
        while bucket and bucket[0] <= now - _RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= max(1, settings.file_qa_rate_limit_per_minute):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="تعداد پرسش‌های فایل زیاد است؛ کمی بعد دوباره تلاش کنید.",
            )
        bucket.append(now)


def _require_enabled() -> None:
    if not settings.file_qa_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="قابلیت پرسش و پاسخ از فایل‌ها فعال نیست.",
        )


def _scope(db: DbSession, user: CurrentUser, project_id: uuid.UUID | None):
    if project_id is not None:
        acl.require_project(db, user, project_id)
        return [project_id], project_id
    return acl.accessible_project_ids(db, user), None


def _validate_file_ids(
    db: DbSession,
    user: CurrentUser,
    file_ids: list[uuid.UUID],
    project_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    unique = list(dict.fromkeys(file_ids))
    if len(unique) > settings.file_qa_max_selected_files:
        raise HTTPException(
            status_code=422,
            detail=f"حداکثر {settings.file_qa_max_selected_files} فایل قابل انتخاب است.",
        )
    for file_id in unique:
        source_file = db.get(SourceFile, file_id)
        if source_file is None:
            raise HTTPException(status_code=404, detail="یکی از فایل‌های انتخاب‌شده یافت نشد.")
        # This check intentionally happens before any query that could reveal
        # filename/index information about an inaccessible file.
        acl.require_project(db, user, source_file.project_id)
        if project_id is not None and source_file.project_id != project_id:
            raise HTTPException(status_code=404, detail="فایل انتخاب‌شده به پروژه جاری تعلق ندارد.")
        suffix = Path(source_file.original_filename).suffix.lower()
        if suffix not in {".pdf", ".docx"}:
            raise HTTPException(status_code=422, detail="فقط فایل‌های PDF و DOCX قابل پرسش هستند.")
    return unique


def _message_out(row: FileQAMessage) -> FileQAMessageOut:
    sources = []
    for source in row.sources or []:
        try:
            sources.append(FileQASourceOut.model_validate(source))
        except Exception:
            # Old/partial history rows should not make the whole conversation
            # unreadable after a deployment upgrade.
            continue
    return FileQAMessageOut(
        id=row.id,
        role=row.role,
        content=row.content,
        sources=sources,
        created_at=row.created_at,
    )


def _history(db: DbSession, session_id: uuid.UUID) -> list[FileQAMessageOut]:
    rows = list(
        db.execute(
            select(FileQAMessage)
            .where(FileQAMessage.session_id == session_id)
            .order_by(FileQAMessage.created_at.desc())
            .limit(max(1, settings.file_qa_max_history))
        ).scalars()
    )
    return [_message_out(row) for row in reversed(rows)]


def _source_payload(hits) -> list[dict[str, Any]]:
    payload = []
    for hit in hits:
        item = hit.as_source()
        # Zero UUID is an internal marker for a request-scoped temporary file; it
        # must never look like an authenticated EDMS download link.
        if item["file_id"] == str(uuid.UUID(int=0)):
            item["file_id"] = None
            item["page_url"] = ""
        payload.append(item)
    return payload


async def _parse_ask_request(request: Request) -> tuple[FileQAAskRequest, Any | None]:
    """Accept JSON for EDMS files and multipart for one temporary upload."""

    content_type = request.headers.get("content-type", "").lower()
    if "multipart/form-data" not in content_type:
        try:
            payload = FileQAAskRequest.model_validate(await request.json())
        except Exception as exc:  # noqa: BLE001 - return normal FastAPI 422
            raise HTTPException(status_code=422, detail="داده پرسش معتبر نیست.") from exc
        return payload, None

    try:
        form = await request.form()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="فرم فایل معتبر نیست.") from exc
    question = str(form.get("question") or "").strip()
    project_raw = str(form.get("project_id") or "").strip() or None
    session_raw = str(form.get("session_id") or "").strip() or None
    raw_ids: list[str] = []
    if hasattr(form, "getlist"):
        raw_ids.extend(str(value) for value in form.getlist("file_ids") if value)
    if not raw_ids and form.get("file_ids"):
        value = str(form.get("file_ids"))
        try:
            parsed = json.loads(value)
            raw_ids = [str(item) for item in parsed] if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            raw_ids = [item.strip() for item in value.split(",") if item.strip()]
    try:
        payload = FileQAAskRequest(
            question=question,
            file_ids=[uuid.UUID(value) for value in raw_ids],
            project_id=uuid.UUID(project_raw) if project_raw else None,
            session_id=uuid.UUID(session_raw) if session_raw else None,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="داده پرسش یا فایل معتبر نیست.") from exc
    upload = form.get("file")
    return payload, upload if getattr(upload, "filename", None) else None


@router.get("/status", response_model=FileQAStatusOut, summary="وضعیت پرسش و پاسخ از فایل‌ها")
def file_qa_status(db: DbSession, user: CurrentUser) -> FileQAStatusOut:
    backend = db.get_bind().dialect.name
    if not settings.file_qa_enabled:
        return FileQAStatusOut(
            enabled=False,
            backend=backend,
            advanced_supported=backend == "postgresql",
            message="قابلیت پرسش و پاسخ از فایل‌ها فعال نیست.",
            embedding_model=settings.file_qa_embedding_model,
            embedding_dimensions=384,
        )
    indexed_files = 0
    migration_ready = True
    try:
        project_ids = acl.accessible_project_ids(db, user)
        clause = [DocumentChunkV2.is_active.is_(True)]
        if project_ids is not None:
            clause.append(DocumentChunkV2.project_id.in_(project_ids) if project_ids else False)
        indexed_files = int(
            db.execute(
                select(func.count(func.distinct(DocumentChunkV2.file_id))).where(and_(*clause))
            ).scalar_one()
            or 0
        )
    except Exception:  # migration not applied yet: status must remain safe
        db.rollback()
        indexed_files = 0
        migration_ready = False
    return FileQAStatusOut(
        enabled=True,
        backend=backend,
        advanced_supported=backend == "postgresql",
        message=(
            "پرسش و پاسخ پیشرفته فقط با PostgreSQL فعال است"
            if backend != "postgresql"
            else ("مهاجرت RAG هنوز روی پایگاه‌داده اجرا نشده است." if not migration_ready else None)
        ),
        embedding_model=settings.file_qa_embedding_model,
        embedding_dimensions=384,
        indexed_files=indexed_files,
    )


@router.get("/documents", response_model=list[FileQADocumentOut], summary="فهرست فایل‌های قابل انتخاب")
def file_qa_documents(
    db: DbSession,
    user: CurrentUser,
    q: str | None = Query(default=None, max_length=200),
    project_id: uuid.UUID | None = None,
) -> list[FileQADocumentOut]:
    _require_enabled()
    project_ids, scoped_project = _scope(db, user, project_id)
    clauses = []
    if scoped_project is not None:
        clauses.append(SourceFile.project_id == scoped_project)
    elif project_ids is not None:
        clauses.append(SourceFile.project_id.in_(project_ids) if project_ids else False)
    clauses.append(SourceFile.original_filename.ilike("%.pdf") | SourceFile.original_filename.ilike("%.docx"))
    if q and q.strip():
        clauses.append(SourceFile.original_filename.ilike(f"%{q.strip()}%"))
    indexed = exists(
        select(DocumentChunkV2.id).where(
            DocumentChunkV2.file_id == SourceFile.id,
            DocumentChunkV2.is_active.is_(True),
        )
    )
    try:
        rows = db.execute(
            select(SourceFile, indexed.label("indexed"))
            .where(and_(*clauses))
            .order_by(SourceFile.updated_at.desc())
            .limit(100)
        ).all()
    except Exception as exc:  # migration may not have been applied yet
        db.rollback()
        raise HTTPException(status_code=503, detail="مهاجرت RAG هنوز روی پایگاه‌داده اجرا نشده است.") from exc
    return [
        FileQADocumentOut(
            id=file.id,
            project_id=file.project_id,
            filename=file.original_filename,
            mime_type=file.mime_type,
            page_count=file.page_count,
            indexed=bool(is_indexed),
            updated_at=file.updated_at,
        )
        for file, is_indexed in rows
    ]


@router.get("/history/{session_id}", response_model=FileQAHistoryOut, summary="تاریخچه نشست پرسش فایل")
def file_qa_history(session_id: uuid.UUID, db: DbSession, user: CurrentUser) -> FileQAHistoryOut:
    _require_enabled()
    conversation = db.get(FileQASession, session_id)
    if conversation is None or conversation.user_id != user.id or not conversation.is_active:
        raise HTTPException(status_code=404, detail="نشست پرسش یافت نشد.")
    return FileQAHistoryOut(session_id=session_id, messages=_history(db, session_id))


@router.post("/ask", response_model=FileQAAskResponse, summary="پرسش مستند از فایل‌ها")
async def file_qa_ask(request: Request, db: DbSession, user: CurrentUser) -> FileQAAskResponse:
    _require_enabled()
    _rate_limit(user.id)
    payload, upload = await _parse_ask_request(request)
    question = payload.question.strip()
    if len(question) > settings.file_qa_max_question_length:
        raise HTTPException(status_code=422, detail="متن پرسش بیش از حد طولانی است.")
    project_ids, scoped_project = _scope(db, user, payload.project_id)
    selected_ids = _validate_file_ids(db, user, payload.file_ids, scoped_project)

    conversation: FileQASession | None = None
    if payload.session_id is not None:
        conversation = db.get(FileQASession, payload.session_id)
        if conversation is None or conversation.user_id != user.id or not conversation.is_active:
            raise HTTPException(status_code=404, detail="نشست پرسش یافت نشد.")
        if scoped_project is not None and conversation.project_id not in {None, scoped_project}:
            raise HTTPException(status_code=404, detail="نشست پرسش به پروژه جاری تعلق ندارد.")
    if conversation is None:
        conversation = FileQASession(user_id=user.id, project_id=scoped_project)
        db.add(conversation)
        db.flush()

    temporary_hits = []
    temp_path: str | None = None
    try:
        if upload is not None:
            filename = str(upload.filename or "").strip()
            suffix = Path(filename).suffix.lower()
            if suffix not in {".pdf", ".docx"}:
                raise HTTPException(status_code=422, detail="فایل موقت باید PDF یا DOCX باشد.")
            data = await upload.read(settings.file_qa_temporary_max_mb * 1024 * 1024 + 1)
            if len(data) > settings.file_qa_temporary_max_mb * 1024 * 1024:
                raise HTTPException(status_code=413, detail="حجم فایل موقت بیش از حد مجاز است.")
            with tempfile.NamedTemporaryFile(prefix="edms-file-qa-", suffix=suffix, delete=False) as handle:
                handle.write(data)
                temp_path = handle.name
            try:
                blocks = extract_document_blocks(temp_path)
                chunks = chunk_blocks(
                    blocks,
                    max_words=220,
                    overlap_words=45,
                    document_name=filename,
                    document_path="",
                )
            except Exception as exc:  # noqa: BLE001 - report a safe input error
                raise HTTPException(status_code=422, detail="متن قابل خواندن از فایل موقت پیدا نشد.") from exc
            temporary_hits = retrieve_temporary_chunks(
                chunks,
                filename,
                question,
                top_k=settings.file_qa_top_k,
                min_score=settings.file_qa_min_score,
                use_semantic=db.get_bind().dialect.name == "postgresql",
            )

        try:
            hits, semantic_used = retrieve_rag_chunks(
                db,
                question,
                project_ids=project_ids,
                project_id=scoped_project,
                file_ids=selected_ids or None,
                top_k=settings.file_qa_top_k,
                min_score=settings.file_qa_min_score,
            )
        except Exception as exc:  # migration/extension errors must degrade clearly
            db.rollback()
            raise HTTPException(status_code=503, detail="فهرست RAG در این محیط آماده نیست.") from exc
        combined = sorted(hits + temporary_hits, key=lambda item: -item.score)[: settings.file_qa_top_k]
        sources_payload = _source_payload(combined)
        try:
            answer = answer_from_hits(question, combined, db) if combined else NO_ANSWER
        except FileQAUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        db.add(FileQAMessage(session_id=conversation.id, role="user", content=question, sources=[]))
        db.add(FileQAMessage(session_id=conversation.id, role="assistant", content=answer, sources=sources_payload))
        conversation.title = question[:255]
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()
        history = _history(db, conversation.id)
        logger.info(
            "file_qa.ask user_id=%s session_id=%s selected_files=%d temporary=%s sources=%d semantic=%s",
            user.id,
            conversation.id,
            len(selected_ids),
            bool(upload),
            len(sources_payload),
            semantic_used,
        )
        return FileQAAskResponse(
            session_id=conversation.id,
            answer=answer,
            sources=[FileQASourceOut.model_validate(item) for item in sources_payload],
            semantic_used=semantic_used or any(hit.semantic for hit in temporary_hits),
            history=history,
        )
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@router.post("/clear", summary="پاک کردن تاریخچه پرسش فایل")
def file_qa_clear(
    db: DbSession,
    user: CurrentUser,
    payload: FileQAClearRequest | None = None,
) -> dict[str, int]:
    _require_enabled()
    session_id = payload.session_id if payload else None
    if session_id is not None:
        conversation = db.get(FileQASession, session_id)
        if conversation is None or conversation.user_id != user.id:
            raise HTTPException(status_code=404, detail="نشست پرسش یافت نشد.")
        conversation.is_active = False
        count = 1
    else:
        rows = db.execute(
            select(FileQASession).where(FileQASession.user_id == user.id, FileQASession.is_active.is_(True))
        ).scalars()
        count = 0
        for row in rows:
            row.is_active = False
            count += 1
    db.commit()
    logger.info("file_qa.clear user_id=%s sessions=%d", user.id, count)
    return {"cleared": count}


@router.post("/admin/rebuild", response_model=list[RAGIndexJobOut], summary="بازفهرست‌سازی RAG فایل‌ها")
@router.post("/rebuild", response_model=list[RAGIndexJobOut], include_in_schema=False)
def file_qa_rebuild(payload: FileQARebuildRequest, db: DbSession, user: CurrentUser) -> list[RAGIndexJobOut]:
    _require_enabled()
    acl.assert_admin(user)
    file_ids = _validate_file_ids(db, user, payload.file_ids, payload.project_id)
    if not file_ids:
        statement = select(SourceFile.id)
        if payload.project_id is not None:
            acl.require_project(db, user, payload.project_id)
            statement = statement.where(SourceFile.project_id == payload.project_id)
        else:
            accessible = acl.accessible_project_ids(db, user)
            if accessible is not None:
                statement = statement.where(SourceFile.project_id.in_(accessible) if accessible else False)
        statement = statement.where(
            SourceFile.original_filename.ilike("%.pdf") | SourceFile.original_filename.ilike("%.docx")
        ).order_by(SourceFile.created_at, SourceFile.id).limit(500)
        file_ids = list(db.execute(statement).scalars())
    jobs = enqueue_rag_index_jobs(
        db,
        file_ids,
        requested_by_id=user.id,
        force=payload.force,
    )
    logger.info("file_qa.rebuild user_id=%s files=%d jobs=%d", user.id, len(file_ids), len(jobs))
    return [RAGIndexJobOut.model_validate(job) for job in jobs]


@router.get("/admin/jobs", response_model=list[RAGIndexJobOut], summary="وضعیت بازفهرست‌سازی RAG")
def file_qa_jobs(db: DbSession, user: CurrentUser) -> list[RAGIndexJobOut]:
    _require_enabled()
    acl.assert_admin(user)
    rows = db.execute(select(RAGIndexJob).order_by(RAGIndexJob.created_at.desc()).limit(100)).scalars()
    return [RAGIndexJobOut.model_validate(row) for row in rows]
