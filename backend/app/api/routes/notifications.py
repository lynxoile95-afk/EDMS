"""In-app notification inbox."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models import NotificationLog
from app.models.enums import NotificationChannel, NotificationStatus
from app.schemas.transmittals import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", summary="کارتابل من")
def inbox(
    db: DbSession,
    user: CurrentUser,
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    stmt = select(NotificationLog).where(
        NotificationLog.user_id == user.id,
        NotificationLog.channel == NotificationChannel.IN_APP,
    )
    if unread_only:
        stmt = stmt.where(NotificationLog.read_at.is_(None))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    unread = db.execute(
        select(func.count(NotificationLog.id)).where(
            NotificationLog.user_id == user.id,
            NotificationLog.channel == NotificationChannel.IN_APP,
            NotificationLog.read_at.is_(None),
        )
    ).scalar_one()
    rows = db.execute(stmt.order_by(NotificationLog.created_at.desc()).limit(limit)).scalars()
    return {
        "total": total,
        "unread": unread,
        "items": [NotificationOut.model_validate(row).model_dump() for row in rows],
    }


@router.post("/{notification_id}/read", status_code=204, summary="علامت‌گذاری به‌عنوان خوانده‌شده")
def mark_read(notification_id: uuid.UUID, db: DbSession, user: CurrentUser) -> None:
    row = db.get(NotificationLog, notification_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="اعلان یافت نشد.")
    row.read_at = datetime.now(timezone.utc)
    row.status = NotificationStatus.READ
    db.commit()


@router.post("/read-all", summary="خواندن همه اعلان‌ها")
def mark_all_read(db: DbSession, user: CurrentUser) -> dict:
    rows = db.execute(
        select(NotificationLog).where(
            NotificationLog.user_id == user.id,
            NotificationLog.channel == NotificationChannel.IN_APP,
            NotificationLog.read_at.is_(None),
        )
    ).scalars()
    count = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        row.read_at = now
        row.status = NotificationStatus.READ
        count += 1
    db.commit()
    return {"updated": count}
