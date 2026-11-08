"""Document revision registration and lifecycle (append-only register)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DocumentRevision,
    ExtractionResult,
    MDLItem,
    Project,
    TransmittalLine,
    User,
)
from app.models.enums import RevisionStatus, TransmittalLineStatus
from app.services import audit
from app.services.normalization import normalize_rev
from app.services.project_settings import merge_settings
from app.services.revisions import (
    EQUAL,
    LOWER,
    compare_revs,
    rev_to_seq,
)


class RevisionConflict(HTTPException):
    def __init__(self, detail: str, code: str = "revision_conflict") -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)
        self.code = code


def register_revision(
    db: Session,
    *,
    project: Project,
    mdl_item: MDLItem,
    rev: str,
    file_hash: str,
    title: str | None,
    page_count: int,
    actor: User,
    source_file_id: uuid.UUID | None = None,
    extraction_result_id: uuid.UUID | None = None,
    notes: str | None = None,
    allow_equal_revision: bool = False,
) -> DocumentRevision:
    """Register a new revision. Idempotent on (mdl_item, rev) and on file hash.

    Raises 409 when the revision contradicts the register, so the API layer can
    return a precise message instead of a generic database error.
    """
    rev_token = normalize_rev(rev)
    if not rev_token:
        raise RevisionConflict("شماره بازنگری معتبر نیست.", "invalid_revision")

    cfg = merge_settings(project.settings)
    scheme = cfg["rev_scheme"]

    # --- idempotency on file hash (same physical file re-submitted) ---------
    existing_hash = db.execute(
        select(DocumentRevision).where(
            DocumentRevision.project_id == project.id, DocumentRevision.file_hash == file_hash
        )
    ).scalar_one_or_none()
    if existing_hash is not None:
        return existing_hash

    current = db.execute(
        select(DocumentRevision)
        .where(DocumentRevision.mdl_item_id == mdl_item.id, DocumentRevision.is_current.is_(True))
        .limit(1)
    ).scalar_one_or_none()
    current_rev = current.rev if current else mdl_item.current_rev

    verdict = compare_revs(current_rev, rev_token, scheme)
    if verdict == LOWER:
        raise RevisionConflict(
            f"بازنگری «{rev_token}» قدیمی‌تر از بازنگری جاری «{current_rev}» است.", "revision_older"
        )
    if verdict == EQUAL and not allow_equal_revision:
        raise RevisionConflict(
            f"بازنگری «{rev_token}» قبلاً ثبت شده است.", "revision_duplicate"
        )

    duplicate = db.execute(
        select(DocumentRevision).where(
            DocumentRevision.mdl_item_id == mdl_item.id, DocumentRevision.rev == rev_token
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        if allow_equal_revision:
            raise RevisionConflict(
                f"بازنگری «{rev_token}» با محتوای متفاوت قبلاً ثبت شده؛ برای اصلاح، بازنگری جدید صادر کنید.",
                "revision_collision",
            )
        return duplicate

    # --- supersede previous -------------------------------------------------
    if current is not None:
        current.is_current = False

    revision = DocumentRevision(
        project_id=project.id,
        mdl_item_id=mdl_item.id,
        rev=rev_token,
        rev_seq=rev_to_seq(rev_token, scheme),
        title_as_drawn=title,
        source_file_id=source_file_id,
        extraction_result_id=extraction_result_id,
        file_hash=file_hash,
        page_count=page_count,
        status=RevisionStatus.REGISTERED,
        is_current=True,
        registered_by_id=actor.id,
        registered_at=datetime.now(timezone.utc),
        notes=notes,
    )
    db.add(revision)
    db.flush()

    if current is not None:
        current.superseded_by_id = revision.id
        db.flush()
        _mark_lines_superseded(db, current.id)

    mdl_item.current_rev = rev_token
    mdl_item.rev_seq = revision.rev_seq
    db.flush()

    audit.record(
        db,
        action="revision.register",
        entity_type="document_revision",
        entity_id=revision.id,
        actor=actor,
        project_id=project.id,
        after={
            "doc_code": mdl_item.doc_code,
            "rev": rev_token,
            "file_hash": file_hash,
            "supersedes": current.rev if current else None,
        },
    )
    return revision


def _mark_lines_superseded(db: Session, revision_id: uuid.UUID) -> None:
    lines = db.execute(
        select(TransmittalLine).where(
            TransmittalLine.revision_id == revision_id,
            TransmittalLine.status.in_([TransmittalLineStatus.DRAFT, TransmittalLineStatus.ISSUED]),
        )
    ).scalars()
    for line in lines:
        line.status = TransmittalLineStatus.SUPERSEDED
    db.flush()


def timeline(db: Session, mdl_item_id: uuid.UUID) -> list[dict]:
    """Revision history for the document detail screen."""
    revisions = db.execute(
        select(DocumentRevision)
        .where(DocumentRevision.mdl_item_id == mdl_item_id)
        .order_by(DocumentRevision.rev_seq.desc())
    ).scalars()
    return [
        {
            "id": str(r.id),
            "rev": r.rev,
            "status": str(r.status),
            "is_current": r.is_current,
            "page_count": r.page_count,
            "registered_at": r.registered_at.isoformat() if r.registered_at else None,
            "file_hash": r.file_hash,
            "source_file_id": str(r.source_file_id) if r.source_file_id else None,
            "notes": r.notes,
        }
        for r in revisions
    ]


def apply_human_corrections(
    db: Session, result: ExtractionResult, corrections: dict, actor: User
) -> ExtractionResult:
    """Store the delta between model output and human input (learning data)."""
    stored: dict = {}
    field_map = {
        "doc_code": "doc_code",
        "rev": "rev",
        "title": "title",
        "discipline_code": "discipline_code",
        "unit_code": "unit_code",
        "doc_status": "doc_status",
    }
    for key, value in corrections.items():
        column = field_map.get(key)
        if column is None:
            continue
        model_value = getattr(result, column)
        if model_value == value:
            continue
        setattr(result, column, value)
        stored[key] = {"model": model_value, "human": value, "by": str(actor.id)}
    if stored:
        result.human_corrections = {**(result.human_corrections or {}), **stored}
        audit.record(
            db,
            action="extraction.correct",
            entity_type="extraction_result",
            entity_id=result.id,
            actor=actor,
            project_id=result.project_id,
            after=stored,
        )
    return result
