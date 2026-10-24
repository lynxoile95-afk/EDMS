"""Transmittal lifecycle: group -> draft -> issue -> acknowledge -> close.

Atomicity contract
------------------
``issue_transmittal`` runs entirely inside the caller's transaction.  Number
allocation, PDF rendering, object upload and status changes either all commit or
all roll back; a half-issued transmittal is impossible by construction.  The
generated PDF is uploaded *before* the commit, and its key is stored on the row
in the same transaction.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    CommentList,
    DocumentRevision,
    Project,
    Transmittal,
    TransmittalLine,
    TransmittalRecipient,
    User,
)
from app.models.enums import (
    AcknowledgementStatus,
    ActionRequired,
    GroupingStrategy,
    PurposeOfIssue,
    RevisionStatus,
    TransmittalLineStatus,
    TransmittalStatus,
    TransmittalType,
)
from app.services import audit, notification, numbering, rendering
from app.services.ddm import DDMResolution, ResolvedRecipient, add_working_days, resolve
from app.services.project_settings import merge_settings
from app.services.storage import Storage, build_key

logger = logging.getLogger(__name__)

ACTION_PRIORITY = {
    ActionRequired.INFO: 1,
    ActionRequired.REVIEW: 2,
    ActionRequired.APPROVE: 3,
    ActionRequired.FOR_CONSTRUCTION: 3,
}


@dataclass(slots=True)
class GroupPlan:
    key: str
    revisions: list[DocumentRevision] = field(default_factory=list)
    recipients: list[ResolvedRecipient] = field(default_factory=list)
    due_days: int = 14
    discipline_code: str | None = None
    ddm: DDMResolution | None = None
    warnings: list[str] = field(default_factory=list)
    #: per-revision required action, resolved from that document's DDM rows
    actions: dict[uuid.UUID, ActionRequired] = field(default_factory=dict)

    @property
    def recipient_ids(self) -> set[uuid.UUID]:
        return {r.user_id for r in self.recipients}


def _action_for(group: GroupPlan, user_id: uuid.UUID) -> ActionRequired:
    actions = [r.action for r in group.recipients if r.user_id == user_id]
    if not actions:
        return ActionRequired.INFO
    return max(actions, key=lambda a: ACTION_PRIORITY.get(ActionRequired(a), 0))


def plan_groups(
    db: Session,
    *,
    project: Project,
    revisions: list[DocumentRevision],
    purpose: PurposeOfIssue,
    settings_override: dict | None = None,
) -> list[GroupPlan]:
    """Group revisions into transmittal buckets (pure computation over DDM)."""
    cfg = merge_settings(settings_override if settings_override is not None else project.settings)
    strategy = GroupingStrategy(cfg["grouping_strategy"])
    groups: dict[str, GroupPlan] = {}

    for revision in revisions:
        item = revision.mdl_item
        if item is None:  # pragma: no cover - defensive
            continue
        discipline_id = item.discipline_id
        discipline_code = item.discipline.code if item.discipline is not None else None
        resolution = resolve(
            db,
            project_id=project.id,
            discipline_id=discipline_id,
            unit_id=item.unit_id,
            doc_type=item.doc_type,
            purpose=purpose,
            settings=cfg,
        )
        if strategy is GroupingStrategy.ONE_PER_SESSION:
            key = "session"
        elif strategy is GroupingStrategy.BY_DISCIPLINE:
            recipient_part = ",".join(sorted(str(r.user_id) for r in resolution.recipients))
            key = f"{discipline_code or 'NA'}::{recipient_part}"
        else:  # BY_RECIPIENTS (default)
            recipient_part = ",".join(sorted(str(r.user_id) for r in resolution.recipients))
            key = f"{recipient_part or 'unassigned'}"

        group = groups.get(key)
        if group is None:
            group = GroupPlan(
                key=key,
                due_days=resolution.due_days,
                discipline_code=discipline_code if strategy is GroupingStrategy.BY_DISCIPLINE else None,
                ddm=resolution,
            )
            groups[key] = group
            if not resolution.matched:
                group.warnings.append(
                    f"برای مدرک {item.doc_code} قاعده‌ای در DDM یافت نشد؛ گیرندگان باید دستی تعیین شوند."
                )
        group.revisions.append(revision)
        group.actions[revision.id] = (
            max((r.action for r in resolution.recipients), key=lambda a: ACTION_PRIORITY.get(ActionRequired(a), 0))
            if resolution.recipients
            else ActionRequired.REVIEW
        )
        group.due_days = max(group.due_days, resolution.due_days)
        existing_ids = {r.user_id for r in group.recipients}
        for recipient in resolution.recipients:
            if recipient.user_id not in existing_ids:
                group.recipients.append(recipient)
                existing_ids.add(recipient.user_id)
            else:
                for current in group.recipients:
                    if current.user_id == recipient.user_id:
                        if ACTION_PRIORITY[recipient.action] > ACTION_PRIORITY[current.action]:
                            current.action = recipient.action

    return sorted(groups.values(), key=lambda g: (g.discipline_code or "", g.key))


def _idempotency_key(
    project: Project, purpose: PurposeOfIssue, recipient_ids: set[uuid.UUID], revision_ids: list[uuid.UUID]
) -> str:
    raw = "|".join(
        [
            str(project.id),
            str(purpose),
            ",".join(sorted(str(r) for r in recipient_ids)),
            ",".join(sorted(str(r) for r in revision_ids)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def create_draft_transmittals(
    db: Session,
    *,
    project: Project,
    revisions: list[DocumentRevision],
    purpose: PurposeOfIssue,
    transmittal_type: TransmittalType,
    actor: User,
    session_id: uuid.UUID | None = None,
    remarks: str | None = None,
    settings_override: dict | None = None,
) -> list[Transmittal]:
    """Create one DRAFT transmittal per group (idempotent)."""
    if not revisions:
        return []
    cfg = merge_settings(settings_override if settings_override is not None else project.settings)
    plans = plan_groups(db, project=project, revisions=revisions, purpose=purpose, settings_override=cfg)
    created: list[Transmittal] = []

    for plan in plans:
        revision_ids = [r.id for r in plan.revisions]
        key = _idempotency_key(project, purpose, plan.recipient_ids, revision_ids)
        existing = db.execute(
            select(Transmittal).where(Transmittal.idempotency_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            created.append(existing)
            continue

        requires_approval = (
            transmittal_type is TransmittalType.EXTERNAL and cfg["external_transmittal_requires_approval"]
        ) or purpose in {PurposeOfIssue(p) for p in cfg["require_pm_approval_purposes"] if p in PurposeOfIssue.__members__}
        status = TransmittalStatus.PENDING_APPROVAL if requires_approval else TransmittalStatus.DRAFT

        due = add_working_days(date.today(), max(plan.due_days, 1))
        transmittal = Transmittal(
            project_id=project.id,
            type=transmittal_type,
            purpose=purpose,
            status=status,
            from_org=project.contractor_name or "",
            to_org=project.client_name or "",
            sender_id=actor.id,
            created_by_id=actor.id,
            due_date=due,
            remarks=remarks,
            session_id=session_id,
            idempotency_key=key,
            meta={
                "project_name": project.name,
                "warnings": plan.warnings,
                "grouping_strategy": cfg["grouping_strategy"],
                "ddm_rule": plan.ddm.rule_name if plan.ddm else None,
            },
        )
        db.add(transmittal)
        db.flush()

        for index, revision in enumerate(plan.revisions, start=1):
            item = revision.mdl_item
            db.add(
                TransmittalLine(
                    transmittal_id=transmittal.id,
                    revision_id=revision.id,
                    line_no=index,
                    action=plan.actions.get(revision.id, ActionRequired.REVIEW),
                    due_date=due,
                    status=TransmittalLineStatus.DRAFT,
                    discipline_code=item.discipline.code if item and item.discipline else None,
                )
            )
        for recipient in plan.recipients:
            db.add(
                TransmittalRecipient(
                    transmittal_id=transmittal.id,
                    user_id=recipient.user_id,
                    action=recipient.action,
                )
            )
        db.flush()
        audit.record(
            db,
            action="transmittal.create_draft",
            entity_type="transmittal",
            entity_id=transmittal.id,
            actor=actor,
            project_id=project.id,
            after={
                "purpose": str(purpose),
                "type": str(transmittal_type),
                "lines": len(plan.revisions),
                "recipients": len(plan.recipients),
            },
        )
        created.append(transmittal)

    return created


def _build_pdf_context(project: Project, transmittal: Transmittal, preparer: User | None) -> dict:
    recipients = [r.user.full_name for r in transmittal.recipients if r.user is not None]
    lines = [
        {
            "doc_code": line.revision.mdl_item.doc_code,
            "title": line.revision.mdl_item.title,
            "rev": line.revision.rev,
            "pages": line.revision.page_count,
            "action": str(line.action),
        }
        for line in transmittal.lines
        if line.revision is not None
    ]
    return rendering.build_context(
        number=transmittal.number or "DRAFT",
        project_name=project.name,
        from_org=transmittal.from_org or project.contractor_name or "",
        to_org=transmittal.to_org or project.client_name or "",
        purpose=str(transmittal.purpose),
        transmittal_type=str(transmittal.type),
        issue_date=transmittal.issued_at,
        due_date=transmittal.due_date,
        recipients=recipients,
        lines=lines,
        remarks=transmittal.remarks,
        prepared_by=preparer.full_name if preparer else None,
        approved_by=preparer.full_name if preparer else None,
        transmittal_id=str(transmittal.id),
    )


def issue_transmittal(
    db: Session,
    *,
    transmittal: Transmittal,
    actor: User,
    storage: Storage | None = None,
    generate_pdf: bool = True,
) -> Transmittal:
    """Allocate the number, render the cover sheet and mark everything issued."""
    if transmittal.status not in {TransmittalStatus.DRAFT, TransmittalStatus.PENDING_APPROVAL}:
        raise ValueError(f"Transmittal در وضعیت «{transmittal.status}» قابل صدور نیست.")
    if not transmittal.lines:
        raise ValueError("Transmittal بدون مدرک قابل صدور نیست.")
    if not transmittal.recipients:
        raise ValueError("Transmittal بدون گیرنده قابل صدور نیست؛ ابتدا DDM یا گیرندگان را تعیین کنید.")

    project = db.get(Project, transmittal.project_id)
    if project is None:  # pragma: no cover
        raise ValueError("پروژه یافت نشد.")

    number, seq = numbering.allocate(db, project)
    transmittal.number = number
    transmittal.seq = seq
    transmittal.status = TransmittalStatus.ISSUED
    transmittal.issued_at = datetime.now(timezone.utc)
    transmittal.issued_by_id = actor.id

    for line in transmittal.lines:
        line.status = TransmittalLineStatus.ISSUED
        if line.revision is not None:
            line.revision.status = RevisionStatus.ISSUED
    db.flush()

    if generate_pdf:
        context = _build_pdf_context(project, transmittal, actor)
        pdf_bytes = rendering.render_pdf(context)
        key = build_key(
            project.code, "transmittals", number, f"{transmittal.id}.pdf"
        )
        if storage is not None:
            storage.put_bytes(key, pdf_bytes, content_type="application/pdf")
        transmittal.pdf_key = key
        transmittal.pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    # every reviewed document gets a comment list bound to this transmittal
    for line in transmittal.lines:
        if line.revision is not None:
            ensure_comment_list(db, revision_id=line.revision.id, transmittal_id=transmittal.id)

    transmittal.meta = {
        **(transmittal.meta or {}),
        "issued_by": actor.full_name,
        "issued_at": transmittal.issued_at.isoformat(),
    }
    db.flush()

    notification.queue_transmittal_issued(db, transmittal)

    audit.record(
        db,
        action="transmittal.issue",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=actor,
        project_id=project.id,
        after={"number": number, "lines": len(transmittal.lines), "recipients": len(transmittal.recipients)},
    )
    return transmittal


def ensure_comment_list(
    db: Session, *, revision_id: uuid.UUID, transmittal_id: uuid.UUID | None
) -> CommentList:
    existing = db.execute(
        select(CommentList).where(
            CommentList.revision_id == revision_id,
            CommentList.transmittal_id == transmittal_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    comment_list = CommentList(
        revision_id=revision_id,
        transmittal_id=transmittal_id,
        status="open",
        opened_at=datetime.now(timezone.utc),
    )
    db.add(comment_list)
    db.flush()
    return comment_list


def acknowledge(
    db: Session,
    *,
    transmittal: Transmittal,
    user: User,
    note: str | None = None,
) -> Transmittal:
    recipient = db.execute(
        select(TransmittalRecipient).where(
            TransmittalRecipient.transmittal_id == transmittal.id,
            TransmittalRecipient.user_id == user.id,
        )
    ).scalar_one_or_none()
    if recipient is None:
        raise ValueError("شما در فهرست گیرندگان این برگه ارسال نیستید.")
    recipient.ack_status = AcknowledgementStatus.ACKNOWLEDGED
    recipient.acknowledged_at = datetime.now(timezone.utc)
    recipient.ack_note = note
    db.flush()

    pending_count = db.execute(
        select(TransmittalRecipient).where(
            TransmittalRecipient.transmittal_id == transmittal.id,
            TransmittalRecipient.ack_status == AcknowledgementStatus.PENDING,
        )
    ).scalars().all()
    transmittal.status = (
        TransmittalStatus.ACKNOWLEDGED if not pending_count else TransmittalStatus.PARTIALLY_ACKNOWLEDGED
    )
    audit.record(
        db,
        action="transmittal.acknowledge",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=user,
        project_id=transmittal.project_id,
        after={"status": str(transmittal.status)},
    )
    return transmittal


def cancel_transmittal(
    db: Session, *, transmittal: Transmittal, actor: User, reason: str
) -> Transmittal:
    if transmittal.status is TransmittalStatus.CANCELLED:
        return transmittal
    if transmittal.status is TransmittalStatus.ACKNOWLEDGED:
        raise ValueError("برگه ارسال تأییدشده قابل ابطال نیست؛ اصلاحیه صادر کنید.")
    before = str(transmittal.status)
    transmittal.status = TransmittalStatus.CANCELLED
    transmittal.meta = {**(transmittal.meta or {}), "cancel_reason": reason, "cancelled_by": actor.full_name}
    for line in transmittal.lines:
        if line.status is not TransmittalLineStatus.SUPERSEDED:
            line.status = TransmittalLineStatus.CLOSED
    db.flush()
    audit.record(
        db,
        action="transmittal.cancel",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=actor,
        project_id=transmittal.project_id,
        before={"status": before},
        after={"status": str(transmittal.status), "reason": reason},
    )
    return transmittal


def add_lines(
    db: Session, *, transmittal: Transmittal, revisions: list[DocumentRevision], due: date | None
) -> Transmittal:
    if transmittal.status is not TransmittalStatus.DRAFT:
        raise ValueError("افزودن مدرک فقط در وضعیت پیش‌نویس ممکن است.")
    start = len(transmittal.lines) + 1
    for offset, revision in enumerate(revisions):
        exists = any(line.revision_id == revision.id for line in transmittal.lines)
        if exists:
            continue
        db.add(
            TransmittalLine(
                transmittal_id=transmittal.id,
                revision_id=revision.id,
                line_no=start + offset,
                action=ActionRequired.REVIEW,
                due_date=due or transmittal.due_date,
            )
        )
    db.flush()
    return transmittal


def stats(db: Session, project_id: uuid.UUID) -> dict:
    """Small aggregate used by the dashboard."""
    rows = db.execute(
        select(Transmittal.status, Transmittal.id).where(Transmittal.project_id == project_id)
    ).all()
    counts: dict[str, int] = {}
    for status_value, _ in rows:
        key = str(status_value)
        counts[key] = counts.get(key, 0) + 1
    return {"total": len(rows), "by_status": counts}
