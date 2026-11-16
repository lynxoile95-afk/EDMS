"""Authenticated, permission-filtered content search and indexing controls."""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.models import IndexJob, SourceFile
from app.schemas.search import (
    AskRequest,
    AskResponse,
    BackfillRequest,
    IndexJobOut,
    ReindexRequest,
    ReindexResponse,
    SearchResponse,
    SearchResultOut,
    SearchStatusOut,
)
from app.search.indexing import enqueue_index_jobs
from app.search.qa import QAUnavailable, answer_with_context
from app.search.query import search_chunks
from app.services import acl

router = APIRouter(prefix="/search", tags=["search"])

_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT = 60


def _rate_limit(user_id: uuid.UUID) -> None:
    now = time.monotonic()
    key = str(user_id)
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS[key]
        while bucket and bucket[0] <= now - _RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="تعداد درخواست‌های جست‌وجو زیاد است؛ کمی بعد دوباره تلاش کنید.",
            )
        bucket.append(now)


def _capability(db: DbSession) -> tuple[str, bool, bool]:
    backend = db.get_bind().dialect.name
    degraded = backend != "postgresql"
    return backend, degraded, backend == "postgresql"


def _require_enabled() -> None:
    if not settings.search_indexing_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="قابلیت جست‌وجوی محتوای سند در این محیط فعال نیست.",
        )


def _bounded_query(value: str | None) -> str:
    query = (value or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="عبارت جست‌وجو را وارد کنید.")
    if len(query) > settings.search_query_max_length:
        raise HTTPException(status_code=422, detail="عبارت جست‌وجو بیش از حد طولانی است.")
    return query


def _project_scope(db: DbSession, user: CurrentUser, project_id: uuid.UUID | None):
    if project_id is not None:
        acl.require_project(db, user, project_id)
        return [project_id], project_id
    return acl.accessible_project_ids(db, user), None


def _jobs_response(jobs: list[IndexJob]) -> ReindexResponse:
    active = [job for job in jobs if job.status in {"queued", "running"}]
    return ReindexResponse(queued=len(active), job_ids=[job.id for job in active])


@router.get("/status", response_model=SearchStatusOut, summary="وضعیت قابلیت جست‌وجوی محتوا")
def search_status(db: DbSession, user: CurrentUser) -> SearchStatusOut:
    backend, degraded, semantic = _capability(db)
    return SearchStatusOut(
        enabled=settings.search_indexing_enabled,
        backend=backend,
        degraded=degraded,
        semantic_supported=semantic,
        message=(
            "پیش‌نمایش SQLite فقط جست‌وجوی کلیدواژه‌ای محدود را پشتیبانی می‌کند."
            if degraded
            else None
        ),
    )


@router.get("", response_model=SearchResponse, summary="جست‌وجوی ترکیبی در مدارک")
def search(
    db: DbSession,
    user: CurrentUser,
    q: str | None = Query(default=None, max_length=300),
    query: str | None = Query(default=None, max_length=300),
    project_id: uuid.UUID | None = None,
    source_type: str | None = Query(default=None, pattern="^(native|ocr)$"),
    include_old_revisions: bool = False,
    page: int = Query(default=1, ge=1),
    limit: int = Query(
        default=settings.search_default_limit,
        ge=1,
        le=settings.search_max_limit,
    ),
) -> SearchResponse:
    _require_enabled()
    _rate_limit(user.id)
    value = _bounded_query(q if q is not None else query)
    project_ids, scoped_project = _project_scope(db, user, project_id)
    backend, degraded, _ = _capability(db)
    items, semantic_used, total = search_chunks(
        db,
        value,
        project_ids=project_ids,
        project_id=scoped_project,
        source_type=source_type,
        limit=limit,
        page=page,
        include_old_revisions=include_old_revisions,
    )
    return SearchResponse(
        enabled=True,
        backend=backend,
        degraded=degraded,
        semantic_used=semantic_used,
        query=value,
        items=[SearchResultOut.model_validate(item) for item in items],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/files/{file_id}/status", response_model=list[IndexJobOut], summary="وضعیت فهرست‌سازی فایل")
def file_index_status(file_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[IndexJobOut]:
    source_file = db.get(SourceFile, file_id)
    if source_file is None:
        raise HTTPException(status_code=404, detail="فایل یافت نشد.")
    acl.require_project(db, user, source_file.project_id)
    rows = db.execute(
        select(IndexJob)
        .where(IndexJob.file_id == file_id)
        .order_by(IndexJob.created_at.desc())
        .limit(20)
    ).scalars()
    return [IndexJobOut.model_validate(row) for row in rows]


@router.post("/admin/reindex", response_model=ReindexResponse, summary="صف‌گذاری بازفهرست‌سازی مدیر")
def admin_reindex(
    payload: ReindexRequest,
    db: DbSession,
    user: CurrentUser,
) -> ReindexResponse:
    acl.assert_admin(user)
    _require_enabled()
    if payload.file_id:
        if db.get(SourceFile, payload.file_id) is None:
            raise HTTPException(status_code=404, detail="فایل یافت نشد.")
        file_ids = [payload.file_id]
    else:
        statement = select(SourceFile.id)
        if payload.project_id is not None:
            statement = statement.where(SourceFile.project_id == payload.project_id)
        statement = statement.order_by(SourceFile.created_at, SourceFile.id).limit(payload.limit)
        file_ids = list(db.execute(statement).scalars())
    jobs = enqueue_index_jobs(db, file_ids, requested_by_id=user.id, force=payload.force)
    return _jobs_response(jobs)


@router.post("/admin/reindex/{file_id}", response_model=ReindexResponse, summary="بازفهرست‌سازی یک فایل")
def admin_reindex_file(file_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ReindexResponse:
    acl.assert_admin(user)
    _require_enabled()
    source_file = db.get(SourceFile, file_id)
    if source_file is None:
        raise HTTPException(status_code=404, detail="فایل یافت نشد.")
    jobs = enqueue_index_jobs(db, [file_id], requested_by_id=user.id, force=True)
    return _jobs_response(jobs)


@router.post("/admin/backfill", response_model=ReindexResponse, summary="بازفهرست‌سازی تدریجی مدارک قدیمی")
def admin_backfill(
    payload: BackfillRequest,
    db: DbSession,
    user: CurrentUser,
) -> ReindexResponse:
    acl.assert_admin(user)
    _require_enabled()
    statement = select(SourceFile.id)
    if payload.project_id is not None:
        statement = statement.where(SourceFile.project_id == payload.project_id)
    statement = (
        statement.order_by(SourceFile.created_at, SourceFile.id)
        .offset(payload.cursor)
        .limit(payload.limit)
    )
    file_ids = list(db.execute(statement).scalars())
    jobs = enqueue_index_jobs(db, file_ids, requested_by_id=user.id, force=payload.force)
    response = _jobs_response(jobs)
    response.next_cursor = payload.cursor + len(file_ids) if len(file_ids) == payload.limit else None
    response.has_more = len(file_ids) == payload.limit
    return response


@router.post("/ask", response_model=AskResponse, summary="پاسخ مستند اختیاری به پرسش")
async def ask(request: AskRequest, db: DbSession, user: CurrentUser) -> AskResponse:
    if not settings.llm_qa_enabled:
        raise HTTPException(status_code=404, detail="پاسخ‌گویی هوشمند فعال نیست.")
    _require_enabled()
    _rate_limit(user.id)
    question = _bounded_query(request.question)
    project_ids, scoped_project = _project_scope(db, user, request.project_id)
    items, _, _ = search_chunks(
        db,
        question,
        project_ids=project_ids,
        project_id=scoped_project,
        limit=min(request.limit, 20),
        page=1,
    )
    if not items:
        raise HTTPException(status_code=404, detail="برای پاسخ، قطعه‌ای از سند پیدا نشد.")
    try:
        answer = await answer_with_context(question, items)
    except QAUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AskResponse(
        answer=answer,
        citations=[SearchResultOut.model_validate(item) for item in items],
    )
