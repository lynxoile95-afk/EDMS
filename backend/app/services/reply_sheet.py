"""Reply Sheet lifecycle and the read model used by the workspace/PDF.

A Reply Sheet is a persisted coordinator form.  Comment rows are copied into the
form at creation time so a completed form is an immutable historical record;
comments themselves remain available in Comment Sheet for later forms.  The
legacy ``reply_sheet_rows`` projection is retained for old transmittals that do
not yet have a persisted form.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Comment,
    CommentList,
    DocumentRevision,
    Project,
    ProjectMember,
    ReplySheet,
    ReplySheetItem,
    Transmittal,
    TransmittalLine,
    User,
)
from app.models.enums import ProjectRole, PurposeOfIssue, RevisionStatus, TransmittalLineStatus, TransmittalStatus
from app.schemas.transmittals import ReplySheetCreate, ReplySheetNoCommentRequest
from app.services import acl, audit
from app.services.project_settings import merge_settings


REPLY_DRAFT = "draft"
REPLY_COMPLETED = "completed"
REPLY_FINAL = "final"
NO_COMMENT = "No Comment"
COMMENTED = "Commented"
UNDER_REVIEW = "Under Review by Provider"
IGNORE = "Ignore"


@dataclass(frozen=True)
class ReplySheetRow:
    """One comment line of the reply sheet read model."""

    doc_code: str | None
    title: str | None
    rev: str | None
    discipline: str | None
    line_no: int
    comment_code: str
    comment_text: str
    author_name: str | None
    response_text: str | None
    disposition: str | None
    status: str
    created_at: datetime
    id: uuid.UUID | None = None
    reply_sheet_id: uuid.UUID | None = None
    transmittal_line_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    comment_id: uuid.UUID | None = None
    judgment: str | None = None
    owner_comments: str | None = None
    provider_clarification: str | None = None
    assign_user_id: uuid.UUID | None = None
    assign_user_name: str | None = None
    doc_status: str | None = None
    doc_percent: int | None = None
    number: str | None = None
    replied_by_id: uuid.UUID | None = None
    replied_by_name: str | None = None
    sheet_created_at: datetime | None = None
    sheet_status: str | None = None
    flow_history_count: int = 0

    def as_dict(self) -> dict:
        return {
            "doc_code": self.doc_code,
            "title": self.title,
            "rev": self.rev,
            "discipline": self.discipline,
            "line_no": self.line_no,
            "comment_code": self.comment_code,
            "comment_text": self.comment_text,
            "author_name": self.author_name,
            "response_text": self.response_text,
            "disposition": self.disposition,
            "status": self.status,
            "created_at": self.created_at,
            "id": self.id,
            "reply_sheet_id": self.reply_sheet_id,
            "transmittal_line_id": self.transmittal_line_id,
            "revision_id": self.revision_id,
            "comment_id": self.comment_id,
            "judgment": self.judgment,
            "owner_comments": self.owner_comments,
            "provider_clarification": self.provider_clarification,
            "assign_user_id": self.assign_user_id,
            "assign_user_name": self.assign_user_name,
            "doc_status": self.doc_status,
            "doc_percent": self.doc_percent,
            "number": self.number,
            "replied_by_id": self.replied_by_id,
            "replied_by_name": self.replied_by_name,
            "sheet_created_at": self.sheet_created_at,
            "sheet_status": self.sheet_status,
            "flow_history_count": self.flow_history_count,
        }


def _value(value: object | None) -> str:
    return str(getattr(value, "value", value) or "")


def _normal_status(value: str | None) -> str:
    token = " ".join((value or COMMENTED).replace("_", " ").replace("-", " ").split()).lower()
    return {
        "no comment": NO_COMMENT,
        "commented": COMMENTED,
        "under review by provider": UNDER_REVIEW,
        "under review": UNDER_REVIEW,
        "ignore": IGNORE,
        "ignored": IGNORE,
    }.get(token, value.strip() if value and value.strip() else COMMENTED)


def _is_coordinator(db: Session, user: User, project_id: uuid.UUID) -> bool:
    if user.is_superuser:
        return True
    roles = acl.roles_in(db, user, project_id)
    return bool(
        roles
        & {
            ProjectRole.DCC.value,
            ProjectRole.PROJECT_CONTROL.value,
            ProjectRole.PMO.value,
            ProjectRole.PROJECT_MANAGER.value,
        }
    ) or any("coordinator" in role.lower() for role in roles)


def assert_coordinator(db: Session, user: User, project_id: uuid.UUID) -> None:
    if not _is_coordinator(db, user, project_id):
        raise acl.AccessDenied("ثبت و تکمیل Reply Sheet فقط برای Coordinator مجاز است.")


def assert_project_manager(db: Session, user: User, project_id: uuid.UUID) -> None:
    # Do not use acl.has_role here: its superuser shortcut is intentionally
    # broader than this business gate.  The customer explicitly limited AFC
    # finalization to the Project Manager role.
    if user.is_superuser:
        roles = acl.roles_in(db, user, project_id)
    else:
        roles = acl.roles_in(db, user, project_id)
    if ProjectRole.PROJECT_MANAGER.value not in roles:
        raise acl.AccessDenied("عملیات No Comment & Finalize فقط برای Project Manager مجاز است.")


def latest_reply_sheet(db: Session, transmittal_id: uuid.UUID) -> ReplySheet | None:
    return db.execute(
        select(ReplySheet)
        .where(ReplySheet.transmittal_id == transmittal_id)
        .order_by(ReplySheet.sequence_no.desc(), ReplySheet.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _sheet_number(transmittal: Transmittal, sequence_no: int) -> str:
    base = transmittal.number or f"DRAFT-{str(transmittal.id)[:8].upper()}"
    return f"{base}-R{sequence_no:02d}"


def flow_history(db: Session, transmittal_id: uuid.UUID, reply_sheet_id: uuid.UUID | None = None) -> list[AuditLog]:
    entity_ids = [str(transmittal_id)]
    entity_types = ["transmittal"]
    if reply_sheet_id is not None:
        entity_ids.append(str(reply_sheet_id))
        entity_types.append("reply_sheet")
    return list(
        db.execute(
            select(AuditLog)
            .where(
                AuditLog.project_id == db.get(Transmittal, transmittal_id).project_id,
                AuditLog.entity_type.in_(entity_types),
                AuditLog.entity_id.in_(entity_ids),
            )
            .order_by(AuditLog.created_at, AuditLog.id)
        ).scalars()
    )


def _controlled_comment(comment: Comment) -> bool:
    judgment = (comment.judgment or "").strip().lower()
    return bool(
        comment.disposition
        or comment.status == "closed"
        or judgment in {"ok", "accepted", "commented", "rejected", "no_comment", "ignore"}
    )


def _project_member(db: Session, project_id: uuid.UUID, user_id: uuid.UUID | None) -> User | None:
    if user_id is None:
        return None
    row = db.execute(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            User.is_active.is_(True),
        )
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=422, detail="کاربر Assign باید عضو فعال همین پروژه باشد.")
    return row


def _line_comments(db: Session, revision_id: uuid.UUID) -> list[Comment]:
    return list(
        db.execute(
            select(Comment)
            .where(Comment.revision_id == revision_id)
            .order_by(Comment.created_at, Comment.seq_no)
        ).scalars()
    )


def _line_for_payload(transmittal: Transmittal, line_id: uuid.UUID) -> TransmittalLine:
    line = next((item for item in transmittal.lines if item.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=422, detail="یکی از ردیف‌های Reply Sheet متعلق به این ترانسمیتال نیست.")
    if line.revision is None:
        raise HTTPException(status_code=422, detail="بازنگری ردیف Reply Sheet یافت نشد.")
    return line


def _reply_sheet_item_row(item: ReplySheetItem, sheet: ReplySheet, flow_count: int) -> ReplySheetRow:
    revision = item.revision
    mdl_item = revision.mdl_item if revision else None
    comment = item.comment
    return ReplySheetRow(
        doc_code=mdl_item.doc_code if mdl_item else None,
        title=mdl_item.title if mdl_item else None,
        rev=revision.rev if revision else None,
        discipline=(
            item.transmittal_line.discipline_code
            if item.transmittal_line and item.transmittal_line.discipline_code
            else mdl_item.discipline.code if mdl_item and mdl_item.discipline else None
        ),
        line_no=item.line_no,
        comment_code=str(comment.code) if comment else "",
        comment_text=comment.text if comment else "",
        author_name=comment.author.full_name if comment and comment.author else None,
        response_text=comment.response_text if comment else None,
        disposition=_value(comment.disposition) if comment and comment.disposition else None,
        status=item.status,
        created_at=(comment.created_at if comment else item.created_at),
        id=item.id,
        reply_sheet_id=sheet.id,
        transmittal_line_id=item.transmittal_line_id,
        revision_id=item.revision_id,
        comment_id=item.comment_id,
        judgment=item.judgment,
        owner_comments=item.owner_comments,
        provider_clarification=item.provider_clarification,
        assign_user_id=item.assign_user_id,
        assign_user_name=item.assign_user.full_name if item.assign_user else None,
        doc_status=item.doc_status,
        doc_percent=item.doc_percent,
        number=sheet.number,
        replied_by_id=sheet.replied_by_id,
        replied_by_name=sheet.replied_by.full_name if sheet.replied_by else None,
        sheet_created_at=sheet.created_at,
        sheet_status=sheet.status,
        flow_history_count=flow_count,
    )


def _legacy_rows(db: Session, transmittal: Transmittal) -> list[ReplySheetRow]:
    rows: list[ReplySheetRow] = []
    for line in sorted(transmittal.lines, key=lambda item: item.line_no):
        revision = line.revision
        if revision is None:
            continue
        item = revision.mdl_item
        for comment in _line_comments(db, revision.id):
            rows.append(
                ReplySheetRow(
                    doc_code=item.doc_code if item else line.doc_code,
                    title=item.title if item else None,
                    rev=revision.rev,
                    discipline=(line.discipline_code if line.discipline_code else item.discipline.code if item and item.discipline else None),
                    line_no=line.line_no,
                    comment_code=str(comment.code),
                    comment_text=comment.text,
                    author_name=comment.author.full_name if comment.author else None,
                    response_text=comment.response_text,
                    disposition=_value(comment.disposition) if comment.disposition else None,
                    status=_value(comment.status),
                    created_at=comment.created_at,
                    id=comment.id,
                    transmittal_line_id=line.id,
                    revision_id=revision.id,
                    comment_id=comment.id,
                    judgment=comment.judgment,
                    doc_status=line.doc_status,
                    doc_percent=line.doc_percent,
                )
            )
    return rows


def reply_sheet_rows(db: Session, transmittal: Transmittal) -> list[ReplySheetRow]:
    """Return the latest persisted form, or the backwards-compatible projection."""
    sheet = latest_reply_sheet(db, transmittal.id)
    if sheet is None:
        return _legacy_rows(db, transmittal)
    history_count = len(flow_history(db, transmittal.id, sheet.id))
    return [
        _reply_sheet_item_row(item, sheet, history_count)
        for item in sheet.items
        if item.included
    ]


def _settings(project) -> dict:
    cfg = merge_settings(project.settings)
    reply_cfg = cfg.get("reply_sheet") or {}
    first_comment = reply_cfg.get("first_form_requires_comment")
    if first_comment is None:
        for key in (
            "first_reply_sheet_requires_comment",
            "require_comment_on_first_reply_sheet",
            "require_comment_first_form",
        ):
            if key in cfg:
                first_comment = cfg[key]
                break
    controlled = reply_cfg.get("coordinator_requires_controlled_comment")
    if controlled is None:
        controlled = cfg.get("coordinator_requires_controlled_comment", True)
    return {
        "first_form_requires_comment": bool(True if first_comment is None else first_comment),
        "coordinator_requires_controlled_comment": bool(controlled),
    }


def _validate_create(
    db: Session,
    transmittal: Transmittal,
    payload: ReplySheetCreate,
) -> tuple[dict[uuid.UUID, object], dict[uuid.UUID, list[Comment]], ReplySheet | None]:
    latest = latest_reply_sheet(db, transmittal.id)
    if _value(transmittal.status) == TransmittalStatus.FINAL.value or (
        latest is not None and latest.status == REPLY_FINAL
    ):
        raise HTTPException(status_code=409, detail="ترانسمیتال Final است و Reply Sheet جدید قابل ایجاد نیست.")
    if latest is not None and latest.status != REPLY_COMPLETED:
        raise HTTPException(status_code=409, detail="تا تکمیل Reply Sheet قبلی، فرم جدید قابل ایجاد نیست.")

    lines = {line.id: line for line in transmittal.lines}
    comments_by_line: dict[uuid.UUID, list[Comment]] = {}
    for line in transmittal.lines:
        if line.revision is not None:
            comments_by_line[line.id] = _line_comments(db, line.revision.id)

    # A missing documents[] payload means "all documents with their current
    # comments", which keeps old clients and the one-click coordinator action
    # compatible with the new persisted form.
    supplied = {entry.line_id: entry for entry in payload.documents}
    if not supplied:
        supplied = {
            line.id: type("Decision", (), {
                "line_id": line.id,
                "status": COMMENTED,
                "judgment": None,
                "comment_ids": [comment.id for comment in comments_by_line.get(line.id, [])],
                "assign_user_id": payload.assigned_to_id,
                "owner_comments": None,
                "provider_clarification": None,
                "doc_status": None,
                "doc_percent": None,
            })()
            for line in transmittal.lines
        }

    for line_id in supplied:
        if line_id not in lines:
            raise HTTPException(status_code=422, detail="یکی از مدارک Reply Sheet متعلق به این ترانسمیتال نیست.")
    return lines, comments_by_line, latest


def create_reply_sheet(
    db: Session,
    *,
    transmittal: Transmittal,
    actor: User,
    payload: ReplySheetCreate | None = None,
    no_comment_payload: ReplySheetNoCommentRequest | None = None,
) -> ReplySheet:
    """Create and optionally complete the next Reply Sheet form."""
    project = db.get(Project, transmittal.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="پروژه یافت نشد.")
    assert_coordinator(db, actor, project.id)

    if no_comment_payload is not None:
        payload = ReplySheetCreate(
            mode="no_comment",
            completed=True,
            assigned_to_id=no_comment_payload.assigned_to_id,
            remarks=no_comment_payload.remarks,
            documents=[],
        )
        selected_revisions = set(no_comment_payload.revision_ids or [])
    else:
        payload = payload or ReplySheetCreate()
        selected_revisions = set()

    lines, comments_by_line, latest = _validate_create(db, transmittal, payload)
    cfg = _settings(project)
    first_form = latest is None
    if first_form and cfg["first_form_requires_comment"] and payload.mode != "no_comment":
        for line_id, line_comments in comments_by_line.items():
            decision = next((entry for entry in payload.documents if entry.line_id == line_id), None)
            status = _normal_status(getattr(decision, "status", COMMENTED))
            if status != NO_COMMENT and not line_comments:
                raise HTTPException(
                    status_code=422,
                    detail="برای ایجاد اولین Reply Sheet، برای هر مدرک حداقل یک Comment لازم است یا No Comment را انتخاب کنید.",
                )

    if payload.assigned_to_id is not None:
        _project_member(db, project.id, payload.assigned_to_id)

    sequence = (latest.sequence_no + 1) if latest is not None else 1
    sheet = ReplySheet(
        project_id=project.id,
        transmittal_id=transmittal.id,
        sequence_no=sequence,
        number=_sheet_number(transmittal, sequence),
        status=REPLY_DRAFT,
        replied_by_id=actor.id,
        created_by_id=actor.id,
        assigned_to_id=payload.assigned_to_id,
        judgment="OK" if payload.mode == "no_comment" else None,
        doc_status=NO_COMMENT if payload.mode == "no_comment" else COMMENTED,
        doc_percent=0,
        no_comment=payload.mode == "no_comment",
        remarks=payload.remarks,
    )
    db.add(sheet)
    db.flush()

    total_percent = 0
    line_count = 0
    ignored_comment_ids: list[str] = []
    for line in transmittal.lines:
        if selected_revisions and line.revision_id not in selected_revisions:
            continue
        decision = payload.documents and next(
            (entry for entry in payload.documents if entry.line_id == line.id), None
        )
        status = NO_COMMENT if payload.mode == "no_comment" else _normal_status(
            getattr(decision, "status", COMMENTED)
        )
        if status not in {NO_COMMENT, COMMENTED, UNDER_REVIEW, IGNORE}:
            raise HTTPException(status_code=422, detail="وضعیت Reply Sheet نامعتبر است.")
        line_comments = comments_by_line.get(line.id, [])
        assign_user_id = getattr(decision, "assign_user_id", None) if decision is not None else None
        if assign_user_id is not None:
            _project_member(db, project.id, assign_user_id)
        requested_ids = set(getattr(decision, "comment_ids", []) or []) if decision is not None else {
            comment.id for comment in line_comments
        }
        line_comment_ids = {comment.id for comment in line_comments}
        if requested_ids - line_comment_ids:
            raise HTTPException(status_code=422, detail="یکی از Commentهای انتخاب‌شده متعلق به همان مدرک نیست.")
        comments = [comment for comment in line_comments if comment.id in requested_ids]

        if status == UNDER_REVIEW and cfg["coordinator_requires_controlled_comment"]:
            if not any(_controlled_comment(comment) for comment in comments):
                raise HTTPException(
                    status_code=422,
                    detail="وضعیت Under Review by Provider حداقل به یک Comment کنترل‌شده نیاز دارد.",
                )

        previous_doc_status = line.doc_status
        percent = getattr(decision, "doc_percent", None)
        if percent is None:
            percent = 100 if status == NO_COMMENT or payload.completed else 0
        doc_status = getattr(decision, "doc_status", None) or (NO_COMMENT if status == NO_COMMENT else status)
        if status == NO_COMMENT:
            percent = 100
            doc_status = NO_COMMENT
            judgment = "OK"
        else:
            judgment = getattr(decision, "judgment", None)

        line.doc_status = doc_status
        line.doc_percent = max(0, min(int(percent), 100))
        audit.record(
            db,
            action="reply_sheet.document_status",
            entity_type="reply_sheet",
            entity_id=sheet.id,
            actor=actor,
            project_id=project.id,
            before={"line_id": str(line.id), "doc_status": previous_doc_status},
            after={
                "line_id": str(line.id),
                "status": status,
                "doc_status": line.doc_status,
                "doc_percent": line.doc_percent,
            },
        )
        line_count += 1
        total_percent += line.doc_percent or 0

        # Ignore is intentionally represented by a document row only. The
        # related comment is omitted from the current form, never deleted from
        # Comment Sheet history.
        if status == IGNORE:
            ignored_comment_ids.extend(str(comment.id) for comment in comments)
            db.add(
                ReplySheetItem(
                    reply_sheet_id=sheet.id,
                    transmittal_line_id=line.id,
                    revision_id=line.revision_id,
                    line_no=line.line_no,
                    included=True,
                    status=IGNORE,
                    judgment=judgment,
                    assign_user_id=getattr(decision, "assign_user_id", None),
                    doc_status=doc_status,
                    doc_percent=line.doc_percent,
                    excluded_reason="Coordinator Ignore",
                )
            )
        elif status == NO_COMMENT:
            db.add(
                ReplySheetItem(
                    reply_sheet_id=sheet.id,
                    transmittal_line_id=line.id,
                    revision_id=line.revision_id,
                    line_no=line.line_no,
                    included=True,
                    status=NO_COMMENT,
                    judgment="OK",
                    assign_user_id=getattr(decision, "assign_user_id", None),
                    doc_status=NO_COMMENT,
                    doc_percent=100,
                )
            )
        else:
            if not comments and status == UNDER_REVIEW:
                # The controlled-comment check above normally catches this;
                # keeping the explicit guard makes the invariant obvious.
                raise HTTPException(status_code=422, detail="Comment کنترل‌شده برای این مدرک یافت نشد.")
            for comment in comments:
                if judgment:
                    comment.judgment = judgment.strip()
                    comment.coordinator_date = datetime.now(timezone.utc).date()
                db.add(
                    ReplySheetItem(
                        reply_sheet_id=sheet.id,
                        transmittal_line_id=line.id,
                        revision_id=line.revision_id,
                        comment_id=comment.id,
                        line_no=line.line_no,
                        included=True,
                        status=status,
                        judgment=judgment or comment.judgment,
                        owner_comments=getattr(decision, "owner_comments", None),
                        provider_clarification=getattr(decision, "provider_clarification", None),
                        assign_user_id=getattr(decision, "assign_user_id", None),
                        doc_status=doc_status,
                        doc_percent=line.doc_percent,
                    )
                )

        comment_lists = db.execute(
            select(CommentList).where(
                CommentList.revision_id == line.revision_id,
                CommentList.transmittal_id == transmittal.id,
            )
        ).scalars()
        for comment_list in comment_lists:
            if payload.completed:
                comment_list.status = REPLY_COMPLETED
                comment_list.closed_at = datetime.now(timezone.utc)

    if line_count == 0:
        raise HTTPException(status_code=422, detail="Reply Sheet باید حداقل یک مدرک داشته باشد.")

    sheet.doc_percent = round(total_percent / line_count)
    sheet.status = REPLY_COMPLETED if payload.completed else REPLY_DRAFT
    sheet.completed_at = datetime.now(timezone.utc) if payload.completed else None
    sheet.completed_by_id = actor.id if payload.completed else None
    if payload.mode == "no_comment":
        sheet.judgment = "OK"
        sheet.doc_status = NO_COMMENT
        sheet.doc_percent = 100
    audit.record(
        db,
        action="reply_sheet.create" if sheet.status == REPLY_DRAFT else "reply_sheet.complete",
        entity_type="reply_sheet",
        entity_id=sheet.id,
        actor=actor,
        project_id=project.id,
        before={"status": None},
        after={
            "status": sheet.status,
            "number": sheet.number,
            "doc_status": sheet.doc_status,
            "doc_percent": sheet.doc_percent,
            "no_comment": sheet.no_comment,
            "ignored_comment_ids": ignored_comment_ids,
        },
    )
    db.flush()
    return sheet


def mark_afc_no_comment_final(
    db: Session,
    *,
    transmittal: Transmittal,
    actor: User,
) -> ReplySheet:
    """Finalize an AFC transmittal after a completed No Comment form."""
    assert_project_manager(db, actor, transmittal.project_id)
    is_afc = _value(transmittal.purpose).upper() == PurposeOfIssue.AFC.value
    is_afc = is_afc or any(
        line.revision is not None
        and line.revision.mdl_item is not None
        and str(line.revision.mdl_item.poi or "").upper() == PurposeOfIssue.AFC.value
        for line in transmittal.lines
    )
    if not is_afc:
        raise HTTPException(status_code=422, detail="No Comment & Finalize فقط برای P.O.I برابر AFC مجاز است.")

    sheet = latest_reply_sheet(db, transmittal.id)
    if sheet is None or sheet.status not in {REPLY_COMPLETED, REPLY_FINAL} or not sheet.no_comment:
        raise HTTPException(status_code=409, detail="ابتدا Reply Sheet نوع No Comment را تکمیل کنید.")

    if transmittal.status == TransmittalStatus.FINAL:
        return sheet

    before = {"transmittal_status": _value(transmittal.status), "sheet_status": sheet.status}
    for line in transmittal.lines:
        revision = line.revision
        if revision is None:
            continue
        line.doc_status = NO_COMMENT
        line.doc_percent = 100
        line.status = TransmittalLineStatus.FINAL
        item = revision.mdl_item
        if item is not None:
            item.status = "Approved"
            item.poi = PurposeOfIssue.AFC.value
            # A final document has one active revision; all earlier rows remain
            # visible in Document Records with the business status Void.
            all_revisions = db.execute(
                select(DocumentRevision)
                .where(DocumentRevision.mdl_item_id == item.id)
                .order_by(DocumentRevision.rev_seq)
            ).scalars()
            for old in all_revisions:
                if old.id == revision.id:
                    old.status = RevisionStatus.FINAL
                    old.is_current = True
                else:
                    old.status = RevisionStatus.VOID
                    old.is_current = False
                    old.superseded_by_id = revision.id
            item.current_rev = revision.rev
            item.rev_seq = revision.rev_seq
        revision.status = RevisionStatus.FINAL
        revision.is_current = True

    transmittal.status = TransmittalStatus.FINAL
    transmittal.stage = "closed"
    transmittal.final_approver_id = actor.id
    transmittal.issued_by_id = transmittal.issued_by_id or actor.id
    transmittal.issued_at = transmittal.issued_at or datetime.now(timezone.utc)
    transmittal.issue_date = transmittal.issue_date or transmittal.issued_at.date()
    transmittal.meta = {
        **(transmittal.meta or {}),
        "final": True,
        "final_status": "Final",
        "finalized_by": actor.full_name,
        "finalized_at": datetime.now(timezone.utc).isoformat(),
        "doc_status": NO_COMMENT,
        "doc_percent": 100,
    }
    sheet.status = REPLY_FINAL
    sheet.completed_at = sheet.completed_at or datetime.now(timezone.utc)
    audit.record(
        db,
        action="transmittal.afc_no_comment_finalize",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=actor,
        project_id=transmittal.project_id,
        before=before,
        after={
            "transmittal_status": TransmittalStatus.FINAL.value,
            "document_status": "Approved",
            "doc_status": NO_COMMENT,
            "doc_percent": 100,
            "status": "Final",
        },
    )
    audit.record(
        db,
        action="reply_sheet.finalize",
        entity_type="reply_sheet",
        entity_id=sheet.id,
        actor=actor,
        project_id=transmittal.project_id,
        before={"status": REPLY_COMPLETED},
        after={"status": REPLY_FINAL, "judgment": "OK", "doc_status": NO_COMMENT, "doc_percent": 100},
    )
    db.flush()
    return sheet


def next_os_revision(db: Session, mdl_item_id: uuid.UUID) -> str:
    revisions = db.execute(
        select(DocumentRevision.rev).where(DocumentRevision.mdl_item_id == mdl_item_id)
    ).scalars()
    numbers: list[int] = []
    for value in revisions:
        token = str(value or "").upper()
        if token.startswith("B") and token[1:].isdigit():
            numbers.append(int(token[1:]))
    return f"B{max(numbers) + 1 if numbers else 0}"


def mark_reply_sheet_status(
    db: Session, *, sheet: ReplySheet, actor: User, status: str, after: dict | None = None
) -> None:
    before = sheet.status
    sheet.status = status
    audit.record(
        db,
        action="reply_sheet.status_change",
        entity_type="reply_sheet",
        entity_id=sheet.id,
        actor=actor,
        project_id=sheet.project_id,
        before={"status": before},
        after={"status": status, **(after or {})},
    )
    db.flush()
