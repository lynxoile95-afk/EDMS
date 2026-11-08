"""Atomic sequence allocation for Transmittal numbers.

Concurrency-safe by construction:

* the counter row is locked with ``SELECT … FOR UPDATE`` inside the caller's
  transaction, so two concurrent issues serialise on the row;
* the formatted number also has a UNIQUE index in the database, so a race can
  never produce duplicates — the second transaction fails and retries;
* a number is *never* reused, even if the transmittal is later cancelled
  (gaps are honest; duplicates are dangerous).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import NumberSequence, Project
from app.services.project_settings import merge_settings
from app.utils.jalali import gregorian_to_jalali

MAX_RETRIES = 5


def _scope_key(scope: str, project: Project, now: datetime) -> str:
    if scope == "monthly":
        jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
        return f"transmittal:{jy}-{jm:02d}"
    if scope == "yearly":
        jy, _, _ = gregorian_to_jalali(now.year, now.month, now.day)
        return f"transmittal:{jy}"
    return f"transmittal:{project.code}"


def _format(template: str, project: Project, seq: int, now: datetime) -> str:
    jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    values = {
        "project": project.code,
        "seq": seq,
        "yy": f"{jy % 100:02d}",
        "yyyy": jy,
        "mm": f"{jm:02d}",
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return f"TRN-{project.code}-{seq:04d}"


def peek_temp(db: Session, project: Project, *, settings: dict | None = None, now: datetime | None = None) -> str:
    """Return the *temporary* number shown before a transmittal is issued.

    Nothing is consumed: this is a display-only placeholder, exactly like the
    ``TM-…-Temp####`` number the reference system puts on a draft.
    """
    cfg = merge_settings(settings if settings is not None else project.settings)
    template = cfg.get("transmittal_temp_number_format") or "TM-{project}-Temp{seq:04d}"
    scope = cfg["transmittal_sequence_scope"]
    now = now or datetime.now()
    scope_key = _scope_key(scope, project, now)
    row = db.execute(select(NumberSequence).where(NumberSequence.scope_key == scope_key)).scalar_one_or_none()
    next_seq = (row.last_value if row is not None else 0) + 1
    return _format(template, project, next_seq, now)


def allocate(db: Session, project: Project, *, settings: dict | None = None, now: datetime | None = None) -> tuple[str, int]:
    """Return ``(number, seq)``. Must be called inside the issuing transaction."""
    cfg = merge_settings(settings if settings is not None else project.settings)
    template = cfg["transmittal_number_format"]
    scope = cfg["transmittal_sequence_scope"]
    now = now or datetime.now()
    scope_key = _scope_key(scope, project, now)

    for attempt in range(MAX_RETRIES):
        stmt = select(NumberSequence).where(
            NumberSequence.project_id == project.id, NumberSequence.scope_key == scope_key
        )
        sequence = db.execute(stmt.with_for_update()).scalar_one_or_none()
        if sequence is None:
            sequence = NumberSequence(project_id=project.id, scope_key=scope_key, next_value=1)
            db.add(sequence)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                continue

        seq = sequence.next_value
        sequence.next_value = seq + 1
        number = _format(template, project, seq, now)
        db.flush()
        return number, seq

    raise RuntimeError("تخصیص شماره Transmittal پس از چند تلاش ناموفق بود.")


def peek_next(db: Session, project: Project, *, settings: dict | None = None, now: datetime | None = None) -> str:
    """Preview the next number without consuming it (for the UI)."""
    cfg = merge_settings(settings if settings is not None else project.settings)
    now = now or datetime.now()
    scope_key = _scope_key(cfg["transmittal_sequence_scope"], project, now)
    sequence = db.execute(
        select(NumberSequence).where(
            NumberSequence.project_id == project.id, NumberSequence.scope_key == scope_key
        )
    ).scalar_one_or_none()
    seq = sequence.next_value if sequence else 1
    return _format(cfg["transmittal_number_format"], project, seq, now)


def next_document_rev_sequence(db: Session, project_id: uuid.UUID) -> int:
    """Unused placeholder kept for symmetry; revisions are numbered per document."""
    return 0
