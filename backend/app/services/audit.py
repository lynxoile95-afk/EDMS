"""Immutable audit trail helpers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def _jsonable(value: Any) -> Any:
    """Coerce values to something JSON-serialisable without leaking secrets."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items() if k.lower() not in {"password", "token", "secret"}}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    actor: User | None = None,
    project_id: uuid.UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    meta: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = False,
) -> AuditLog:
    """Append an audit entry to the current transaction (caller commits)."""
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        project_id=project_id,
        before=_jsonable(before) if before is not None else {},
        after=_jsonable(after) if after is not None else {},
        meta=_jsonable(meta) if meta is not None else {},
        ip_address=ip_address,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry


def audit_from_request(db: Session, request, **kwargs) -> AuditLog:
    """Convenience wrapper pulling actor/IP/UA from a FastAPI request."""
    actor = getattr(request.state, "user", None)
    client = getattr(request, "client", None)
    return record(
        db,
        actor=actor,
        ip_address=getattr(client, "host", None),
        user_agent=(request.headers.get("user-agent") if request is not None else None),
        **kwargs,
    )
