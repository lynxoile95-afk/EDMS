"""Authenticated PDF document-review markup endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.deps import CurrentUser, DbSession
from app.schemas.markups import (
    MarkupAnnotationsSave,
    MarkupCreate,
    MarkupReply,
    MarkupSessionOut,
    MarkupSubmitOut,
)
from app.services import markup_service

router = APIRouter(prefix="/markups", tags=["document-markup"])


@router.post("", response_model=MarkupSessionOut, status_code=201, summary="ایجاد جلسه بازبینی PDF")
def create_markup(payload: MarkupCreate, db: DbSession, user: CurrentUser) -> MarkupSessionOut:
    session = markup_service.create_or_get(db, user, payload)
    db.commit()
    db.refresh(session)
    return markup_service.session_out(db, session, user)


@router.get("", response_model=list[MarkupSessionOut], summary="فهرست جلسات نشانه‌گذاری یک فایل")
def list_markups(
    db: DbSession,
    user: CurrentUser,
    file_id: uuid.UUID = Query(..., description="شناسه پیوست PDF ترانسمیتال"),
    transmittal_id: uuid.UUID | None = Query(None),
) -> list[MarkupSessionOut]:
    sessions = markup_service.list_for_file(db, user, file_id, transmittal_id)
    return [markup_service.session_out(db, session, user) for session in sessions]


@router.get("/{markup_id}", response_model=MarkupSessionOut, summary="مشاهده جلسه نشانه‌گذاری")
def get_markup(markup_id: uuid.UUID, db: DbSession, user: CurrentUser) -> MarkupSessionOut:
    session = markup_service.get_authorized(db, user, markup_id)
    return markup_service.session_out(db, session, user)


@router.put("/{markup_id}/annotations", response_model=MarkupSessionOut, summary="ذخیره پیش‌نویس نشانه‌گذاری")
def save_markup_annotations(
    markup_id: uuid.UUID,
    payload: MarkupAnnotationsSave,
    db: DbSession,
    user: CurrentUser,
) -> MarkupSessionOut:
    session = markup_service.get_authorized(db, user, markup_id)
    markup_service.save_annotations(db, user, session, payload)
    db.commit()
    db.refresh(session)
    return markup_service.session_out(db, session, user)


@router.post("/{markup_id}/submit", response_model=MarkupSubmitOut, summary="ثبت نهایی و تولید فایل‌های Comment")
def submit_markup(markup_id: uuid.UUID, db: DbSession, user: CurrentUser) -> MarkupSubmitOut:
    session = markup_service.get_authorized(db, user, markup_id)
    try:
        result = markup_service.submit(db, user, session)
        db.commit()
        db.refresh(session)
        return result
    except Exception:
        db.rollback()
        raise


@router.post(
    "/{markup_id}/annotations/{number}/reply",
    response_model=MarkupSessionOut,
    summary="پاسخ مالک به یک نشانه‌گذاری",
)
def reply_markup(
    markup_id: uuid.UUID,
    number: int,
    payload: MarkupReply,
    db: DbSession,
    user: CurrentUser,
) -> MarkupSessionOut:
    if number < 1:
        raise HTTPException(status_code=422, detail="شماره نظر نامعتبر است.")
    session = markup_service.get_authorized(db, user, markup_id)
    markup_service.reply(db, user, session, number, payload)
    db.commit()
    db.refresh(session)
    return markup_service.session_out(db, session, user)


@router.delete("/{markup_id}", status_code=204, summary="حذف مدیریتی جلسه نشانه‌گذاری")
def delete_markup(markup_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    session = markup_service.get_authorized(db, user, markup_id)
    markup_service.delete(db, user, session)
    db.commit()
    return Response(status_code=204)
