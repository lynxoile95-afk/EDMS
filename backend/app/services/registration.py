"""Transmittal registration: build the sheet from uploaded PDFs, then save it.

The screen is a staging area. Each uploaded file becomes a :class:`RegistrationRow`
holding what the model read, what the operator confirmed, and the verdict of the
code-side validation. Nothing touches the document register until ``save_rows`` —
so re-extracting, editing and removing rows are all safe.

Validation is deliberately *not* delegated to the model:

* the document number and the revision must match the project's regexes,
* a revision is compared with the one already in the register
  (:func:`app.services.revisions.compare_revs`, which orders A0 < A1 < … < A9 < B0):
  higher supersedes, equal is a duplicate warning, lower is blocked,
* a file whose bytes are already registered is flagged,
* any field below ``confidence_threshold``, or any model-reported conflict, puts the
  row in «نیازمند بررسی» and blocks the final save until the operator confirms it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DocumentRevision,
    MDLItem,
    Project,
    RegistrationRow,
    SourceFile,
    Transmittal,
    TransmittalLine,
    UploadSession,
    User,
)
from app.models.enums import (
    ProjectRole,
    PurposeOfIssue,
    RevisionStatus,
    TransmittalLineStatus,
    TransmittalStatus,
    TransmittalType,
    UploadStatus,
    FileStatus,
)
from app.services import acl, audit
from app.services.metadata_extraction import ExtractionOutcome, extract_metadata
from app.services.normalization import normalize_code, normalize_title
from app.services.project_settings import merge_settings
from app.services.revisions import compare_revs, rev_to_seq

REGISTRATION_KIND = "registration"


class RegistrationError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.detail = message
        self.status_code = status_code


@dataclass(slots=True)
class RowValidation:
    state: str = "new"  # ok | new | supersede | duplicate | blocked
    messages: list[str] = field(default_factory=list)
    needs_review: bool = False
    low_confidence: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"


# --------------------------------------------------------------------- container
def get_registration(db: Session, project: Project) -> Transmittal | None:
    """The one in-progress registration of this project, if any."""
    rows = (
        db.execute(
            select(Transmittal)
            .where(
                Transmittal.project_id == project.id,
                Transmittal.status == TransmittalStatus.DRAFT,
                Transmittal.stage == "draft",
            )
            .order_by(Transmittal.created_at.desc())
        )
        .scalars()
        .all()
    )
    for row in rows:
        if (row.meta or {}).get("kind") == REGISTRATION_KIND:
            return row
    return None


def create_registration(
    db: Session, *, project: Project, user: User, purpose: PurposeOfIssue = PurposeOfIssue.IFR
) -> Transmittal:
    session = UploadSession(
        project_id=project.id,
        created_by_id=user.id,
        purpose=purpose,
        mode="new_document",
        transmittal_type="internal",
        note="transmittal registration",
        status=UploadStatus.UPLOADING,
        stats={"files": 0, "bytes": 0},
    )
    db.add(session)
    db.flush()
    transmittal = Transmittal(
        project_id=project.id,
        type=TransmittalType.INTERNAL,
        purpose=purpose,
        status=TransmittalStatus.DRAFT,
        stage="draft",
        created_by_id=user.id,
        sender_id=user.id,
        session_id=session.id,
        remarks=None,
        meta={"kind": REGISTRATION_KIND},
    )
    db.add(transmittal)
    db.flush()
    audit.record(
        db, action="registration.create", entity_type="transmittal",
        entity_id=transmittal.id, actor=user, project_id=project.id,
        after={"purpose": str(purpose)},
    )
    return transmittal


def next_row_no(db: Session, transmittal_id: uuid.UUID) -> int:
    rows = db.execute(
        select(RegistrationRow.row_no).where(RegistrationRow.transmittal_id == transmittal_id)
    ).scalars().all()
    return (max(rows) + 1) if rows else 1


# ------------------------------------------------------------------ row creation
def add_row_from_extraction(
    db: Session,
    *,
    project: Project,
    transmittal: Transmittal,
    source_file: SourceFile,
    page_count: int,
    outcome: ExtractionOutcome,
    settings: dict,
) -> RegistrationRow:
    """Create a row from one file: model output where available, empty where not."""
    cfg = settings["registration"]
    data = outcome.data or {}
    discipline = (data.get("discipline") or "").strip().upper() or None
    labels = cfg.get("discipline_labels") or {}

    row = RegistrationRow(
        transmittal_id=transmittal.id,
        project_id=project.id,
        source_file_id=source_file.id,
        row_no=next_row_no(db, transmittal.id),
        filename=source_file.original_filename,
        file_sha256=source_file.sha256,
        page_count=page_count,
        doc_no=data.get("doc_number"),
        title=data.get("title"),
        discipline_code=discipline,
        discipline_label=labels.get(discipline) if discipline else None,
        version=data.get("revision"),
        poi=data.get("purpose_of_issue"),
        doc_status=None,  # never from the model
        doc_percent=0,
        mdl_status=None,
        confidentiality=cfg.get("default_confidentiality") or "Normal",
        extraction_status="ok" if outcome.ok else "manual",
        extraction_error=outcome.error,
        extraction_model=outcome.model,
        extraction_attempts=outcome.attempts,
        extracted_pages=[outcome.page_used] if outcome.page_used else [],
        extracted=outcome.raw,
        confidence=outcome.confidence,
        conflicts=outcome.conflicts,
        warnings=[],
    )
    if not outcome.ok:
        # "failed" = we tried and something broke; "manual" = nobody has tried yet
        row.extraction_status = "failed" if outcome.error else "manual"
        if outcome.error:
            row.warnings = [outcome.error]
    db.add(row)
    db.flush()
    return row


def extract_for_row(db: Session, *, row: RegistrationRow, settings: dict) -> ExtractionOutcome:
    """(Re-)run the model on a row that already has a stored file."""
    source = db.get(SourceFile, row.source_file_id) if row.source_file_id else None
    if source is None:
        raise RegistrationError("فایلی برای این ردیف ذخیره نشده است.")
    cfg = settings["registration"]
    path, cleanup = materialise(source)
    try:
        outcome = extract_metadata(
            path,
            pages=list(cfg.get("extraction_pages") or [1, 2]),
            retries=int(cfg.get("extraction_retries", 2)),
        )
    finally:
        cleanup()
    _apply_outcome(row, outcome, settings)
    # the operator asked again: a fresh answer deserves a fresh confirmation
    row.review_confirmed = False
    validate_row(db, project=db.get(Project, row.project_id), row=row, settings=settings)
    db.flush()
    return outcome


def _apply_outcome(row: RegistrationRow, outcome: ExtractionOutcome, settings: dict) -> None:
    cfg = settings["registration"]
    data = outcome.data or {}
    labels = cfg.get("discipline_labels") or {}
    row.extraction_status = "ok" if outcome.ok else "failed"
    row.extraction_error = outcome.error
    row.extraction_model = outcome.model
    row.extraction_attempts = outcome.attempts
    row.extracted_pages = [outcome.page_used] if outcome.page_used else []
    row.extracted = outcome.raw
    row.confidence = outcome.confidence
    row.conflicts = outcome.conflicts
    if outcome.ok:
        discipline = (data.get("discipline") or "").strip().upper() or None
        row.doc_no = data.get("doc_number")
        row.title = data.get("title")
        row.discipline_code = discipline
        row.discipline_label = labels.get(discipline) if discipline else None
        row.version = data.get("revision")
        row.poi = data.get("purpose_of_issue")
        # Doc. Status / % / MDL Status / Confidentiality are never taken from the model


# --------------------------------------------------------------------- validation
def validate_row(db: Session, *, project: Project, row: RegistrationRow, settings: dict) -> RowValidation:
    """The code-side checks — the model plays no part in any of them."""
    cfg = settings["registration"]
    result = RowValidation()
    threshold = float(cfg.get("confidence_threshold", 0.8))
    doc_re = cfg.get("doc_number_regex") or ""
    rev_re = cfg.get("revision_regex") or ""

    doc_no = (row.doc_no or "").strip().upper()
    version = (row.version or "").strip().upper()

    if not doc_no:
        result.messages.append("شماره مدرک خالی است.")
        result.state = "blocked"
    elif doc_re and not re.match(doc_re, doc_no):
        result.messages.append(f"شماره مدرک با الگوی پروژه سازگار نیست: {doc_no}")
        result.state = "blocked"

    if not version:
        result.messages.append("بازنگری (Version) خالی است.")
        result.state = "blocked"
    elif rev_re and not re.match(rev_re, version):
        result.messages.append(f"بازنگری با الگوی پروژه سازگار نیست: {version}")
        result.state = "blocked"

    # ---- confidence / conflicts -> «نیازمند بررسی»
    low = sorted(
        key for key, value in (row.confidence or {}).items() if isinstance(value, (int, float)) and value < threshold
    )
    result.low_confidence = low
    if low:
        result.messages.append(
            "اطمینان مدل برای این فیلدها کمتر از "
            f"{int(threshold * 100)}٪ است: {', '.join(low)}"
        )
    if row.conflicts:
        result.messages.append("مدل مغایرت گزارش کرده است: " + "؛ ".join(row.conflicts))
    result.needs_review = bool(low or row.conflicts)

    # ---- same bytes already registered
    if row.file_sha256:
        duplicate_file = db.execute(
            select(DocumentRevision).where(
                DocumentRevision.project_id == project.id,
                DocumentRevision.file_hash == row.file_sha256,
            )
        ).scalars().first()
        if duplicate_file is not None:
            result.messages.append(
                f"این فایل قبلاً به‌عنوان بازنگری {duplicate_file.rev} ثبت شده است."
            )
            if result.state != "blocked":
                result.state = "duplicate"

    # ---- the same bytes twice on this very sheet (the file hash is computed at upload)
    if row.file_sha256:
        twin = db.execute(
            select(RegistrationRow)
            .where(
                RegistrationRow.transmittal_id == row.transmittal_id,
                RegistrationRow.file_sha256 == row.file_sha256,
                RegistrationRow.id != row.id,
            )
            .order_by(RegistrationRow.row_no)
            .limit(1)
        ).scalars().first()
        if twin is not None:
            result.messages.append(
                f"همین فایل در همین برگه در ردیف {twin.row_no} هم بارگذاری شده است."
            )
            if result.state != "blocked":
                result.state = "duplicate"

    # ---- revision against the register
    if doc_no and version:
        mdl_item = db.execute(
            select(MDLItem).where(
                MDLItem.project_id == project.id, MDLItem.doc_code_norm == normalize_code(doc_no)
            )
        ).scalar_one_or_none()
        if mdl_item is None:
            row.mdl_status = None
            if result.state not in {"blocked", "duplicate"}:
                result.state = "new"
            result.messages.append("این مدرک در MDL نیست و هنگام ذخیره به آن افزوده می‌شود.")
        else:
            row.mdl_status = mdl_item.status or mdl_item.current_rev or "در MDL"
            current = mdl_item.current_rev
            verdict = compare_revs(current, version, settings.get("rev_scheme", "auto"))
            if verdict == "LOWER":
                result.state = "blocked"
                result.messages.append(
                    f"بازنگری جدید ({version}) از بازنگری جاری ({current}) پایین‌تر است؛ ثبت مجاز نیست."
                )
            elif verdict == "EQUAL":
                if result.state != "blocked":
                    result.state = "duplicate"
                result.messages.append(
                    f"بازنگری {version} قبلاً برای این مدرک ثبت شده است (تکراری)."
                )
            elif verdict in {"GREATER", "SKIP"}:
                if result.state not in {"blocked", "duplicate"}:
                    result.state = "supersede"
                result.messages.append(
                    f"بازنگری {current} با {version} جایگزین (Superseded) می‌شود."
                )
            else:  # NEW
                if result.state not in {"blocked", "duplicate"}:
                    result.state = "new"

    row.validation_state = result.state
    row.validation_messages = result.messages
    row.needs_review = result.needs_review or row.needs_review and not row.review_confirmed
    if result.blocked:
        # a blocked row can never be considered confirmed
        row.review_confirmed = False
    row.validated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


# ------------------------------------------------------------------------- save
def save_rows(
    db: Session, *, project: Project, transmittal: Transmittal, user: User, settings: dict, row_ids=None
) -> dict:
    """Write the confirmed rows into the register and the transmittal, atomically."""
    stmt = select(RegistrationRow).where(
        RegistrationRow.transmittal_id == transmittal.id, RegistrationRow.saved.is_(False)
    )
    if row_ids:
        stmt = stmt.where(RegistrationRow.id.in_(row_ids))
    rows = list(db.execute(stmt.order_by(RegistrationRow.row_no)).scalars())

    if not rows:
        raise RegistrationError("ردیفی برای ذخیره وجود ندارد.")

    pending = [
        row
        for row in rows
        if (validate_row(db, project=project, row=row, settings=settings).needs_review and not row.review_confirmed)
    ]
    blocked = [
        row for row in rows if validate_row(db, project=project, row=row, settings=settings).blocked
    ]
    if blocked:
        raise RegistrationError(
            "این ردیف‌ها قابل ثبت نیستند: " + "، ".join(row.doc_no or row.filename for row in blocked)
        )
    if pending:
        raise RegistrationError(
            "این ردیف‌ها نیازمند بررسی و تأیید شما هستند: "
            + "، ".join(row.doc_no or row.filename for row in pending)
        )

    saved: list[dict] = []
    for row in rows:
        mdl_item = db.execute(
            select(MDLItem).where(
                MDLItem.project_id == project.id,
                MDLItem.doc_code_norm == normalize_code(row.doc_no or ""),
            )
        ).scalar_one_or_none()
        created_mdl = False
        if mdl_item is None:
            mdl_item = MDLItem(
                project_id=project.id,
                doc_code=(row.doc_no or "").strip().upper(),
                doc_code_norm=normalize_code(row.doc_no or ""),
                title=(row.title or row.doc_no or "").strip(),
                title_norm=normalize_title(row.title),
                doc_type=None,
                current_rev=(row.version or "").strip().upper(),
                rev_seq=rev_to_seq(row.version, settings.get("rev_scheme", "auto")),
                status=row.doc_status or "Draft",
                poi=row.poi,
                weight=1.0,
                is_active=True,
            )
            db.add(mdl_item)
            db.flush()
            created_mdl = True

        existing = {
            rev.rev.strip().upper(): rev
            for rev in db.execute(
                select(DocumentRevision).where(DocumentRevision.mdl_item_id == mdl_item.id)
            ).scalars()
        }
        version = (row.version or "").strip().upper()
        if version in existing:
            raise RegistrationError(
                f"بازنگری {version} برای مدرک {row.doc_no} قبلاً ثبت شده است."
            )

        revision = DocumentRevision(
            project_id=project.id,
            mdl_item_id=mdl_item.id,
            rev=version,
            rev_seq=rev_to_seq(version, settings.get("rev_scheme", "auto")),
            title_as_drawn=row.title,
            source_file_id=row.source_file_id,
            file_hash=row.file_sha256 or f"{uuid.uuid4().hex}{uuid.uuid4().hex}",
            page_count=row.page_count or 1,
            status=RevisionStatus.REGISTERED,
            is_current=True,
            registered_by_id=user.id,
            registered_at=datetime.now(timezone.utc),
            notes="ثبت از صفحهٔ «ثبت و بارگذاری مدرک»",
        )
        db.add(revision)
        db.flush()

        # supersede the previous current revision, if this one is newer
        previous_current = [rev for rev in existing.values() if rev.is_current]
        verdict = compare_revs(mdl_item.current_rev, version, settings.get("rev_scheme", "auto"))
        if verdict in {"GREATER", "SKIP"}:
            for old in previous_current:
                old.is_current = False
                old.status = RevisionStatus.SUPERSEDED
                old.superseded_by_id = revision.id

        mdl_item.current_rev = version
        mdl_item.rev_seq = revision.rev_seq
        if row.title:
            mdl_item.title = row.title
            mdl_item.title_norm = normalize_title(row.title)
        if created_mdl:
            mdl_item.status = row.doc_status or "Draft"
        if row.discipline_code and mdl_item.discipline_id is None:
            from app.models import Discipline

            discipline = db.execute(
                select(Discipline).where(
                    Discipline.project_id == project.id, Discipline.code == row.discipline_code
                )
            ).scalar_one_or_none()
            if discipline is not None:
                mdl_item.discipline_id = discipline.id

        db.add(
            TransmittalLine(
                transmittal_id=transmittal.id,
                revision_id=revision.id,
                line_no=row.row_no,
                status=TransmittalLineStatus.DRAFT,
                discipline_code=row.discipline_code,
                note=row.doc_status,
            )
        )
        row.revision_id = revision.id
        row.saved = True
        row.mdl_status = mdl_item.status
        saved.append(
            {
                "row_no": row.row_no,
                "doc_no": row.doc_no,
                "version": version,
                "revision_id": str(revision.id),
                "mdl_created": created_mdl,
                "superseded": verdict in {"GREATER", "SKIP"},
            }
        )

    transmittal.status = TransmittalStatus.DRAFT
    audit.record(
        db, action="registration.save", entity_type="transmittal", entity_id=transmittal.id,
        actor=user, project_id=project.id,
        after={"rows": len(saved), "documents": [item["doc_no"] for item in saved]},
    )
    db.flush()
    return {"saved": saved, "count": len(saved)}


def row_to_dict(row: RegistrationRow) -> dict:
    """The shape the registration screen reads (one row of the table + its proofs)."""
    source = row.source_file
    return {
        "id": str(row.id),
        "row_no": row.row_no,
        "filename": row.filename,
        "source_file_id": str(row.source_file_id) if row.source_file_id else None,
        "page_count": row.page_count,
        "doc_no": row.doc_no,
        "title": row.title,
        "discipline_code": row.discipline_code,
        "discipline_label": row.discipline_label,
        "version": row.version,
        "poi": row.poi,
        "doc_status": row.doc_status,
        "doc_percent": row.doc_percent,
        "mdl_status": row.mdl_status,
        "confidentiality": row.confidentiality,
        "extraction_status": row.extraction_status,
        "extraction_error": row.extraction_error,
        "extraction_model": row.extraction_model,
        "extraction_attempts": row.extraction_attempts,
        "extracted_pages": row.extracted_pages,
        "confidence": row.confidence,
        "conflicts": row.conflicts,
        "warnings": row.warnings,
        "needs_review": row.needs_review,
        "review_confirmed": row.review_confirmed,
        "validation_state": row.validation_state,
        "validation_messages": row.validation_messages,
        "saved": row.saved,
        "revision_id": str(row.revision_id) if row.revision_id else None,
        "file_url": f"/api/v1/files/source-files/{row.source_file_id}" if row.source_file_id else None,
        "sha256": row.file_sha256,
        "page_count_source": getattr(source, "page_count", None),
    }


def settings_for(project: Project) -> dict:
    return merge_settings(project.settings)


def require_writer(db: Session, user: User, project: Project) -> None:
    """Registration writes the register, so it is document-control work."""
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])


def materialise(source: SourceFile) -> tuple[str, "object"]:
    """Copy a stored file to a temp path so PyMuPDF can open it (S3 or local)."""
    import os
    import tempfile

    from app.services.storage import get_storage

    handle, path = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    try:
        get_storage().download_to(source.storage_key, path)
    except Exception:
        os.unlink(path)
        raise
    return path, lambda: os.path.exists(path) and os.unlink(path)
