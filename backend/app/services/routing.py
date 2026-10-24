"""The review route: contractor → document control → management → discipline.

The organisation's procedure, as implemented here:

1. the **contractor** assembles a transmittal (stage ``draft``);
2. issuing it places the sheet in **document control** (stage ``dcc``), which
   checks the content, attaches the transmittal and forwards it;
3. **document control** forwards to the **project manager / engineering manager**
   (stage ``management``);
4. the **engineering manager** refers it to one of the four disciplines —
   Electrical, Civil, Architecture, Utilities — which resolves to that
   discipline's engineer (stage ``discipline``);
5. the discipline engineer reports back, and the sheet returns to management.

Every hop writes a :class:`TransmittalReferral` row, so the flow history the
reference system prints («گردش مدرک») is real data rather than a rendering of
the recipient list.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Discipline, Project, ProjectMember, Transmittal, TransmittalReferral, User
from app.models.enums import ActionRequired, ProjectRole, ReferralStatus, TransmittalStage
from app.services import audit, notification


class RoutingError(ValueError):
    """A refused transition, with a Persian message ready for the API layer."""


#: Persian captions for the stage badge in the UI and in notifications.
STAGE_LABELS = {
    TransmittalStage.DRAFT: "پیش‌نویس — نزد پیمانکار",
    TransmittalStage.DCC: "در حال کنترل — کارشناس کنترل اسناد",
    TransmittalStage.MANAGEMENT: "در حال بررسی — مدیر مهندسی / مدیر پروژه",
    TransmittalStage.DISCIPLINE: "در حال بررسی — کارشناس دیسیپلین",
    TransmittalStage.CLOSED: "بسته‌شده",
}

ACTION_LABELS_FA = {
    ActionRequired.REVIEW: "بررسی و اعلام نظر",
    ActionRequired.APPROVE: "تأیید",
    ActionRequired.INFO: "اطلاع",
    ActionRequired.FOR_CONSTRUCTION: "اجرا",
}


def _project_manager_of(db: Session, project_id: uuid.UUID, role: ProjectRole) -> User | None:
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.role == role)
        .order_by(ProjectMember.created_at)
        .first()
    )
    return member.user if member else None


def engineer_of_discipline(db: Session, project_id: uuid.UUID, discipline_id: uuid.UUID) -> User | None:
    """The reviewer explicitly assigned to a discipline (None when nobody is)."""
    member = (
        db.query(ProjectMember)
        .filter(
            ProjectMember.project_id == project_id,
            ProjectMember.role == ProjectRole.REVIEWER,
            ProjectMember.discipline_id == discipline_id,
        )
        .order_by(ProjectMember.created_at)
        .first()
    )
    return member.user if member else None


def _notify(
    db: Session,
    *,
    transmittal: Transmittal,
    user: User,
    template: str,
    action: str,
    note: str | None,
    actor: User,
    referral: TransmittalReferral,
) -> None:
    notification.queue(
        db,
        user=user,
        template=template,
        context={
            "number": transmittal.number or "پیش‌نویس",
            "project": transmittal.meta.get("project_name", ""),
            "from": actor.full_name,
            "action_fa": action,
            "note": note or "—",
            "link": f"/transmittals/{transmittal.id}",
        },
        project_id=transmittal.project_id,
        entity_type="transmittal",
        entity_id=str(transmittal.id),
        dedupe_key=f"referral:{referral.id}:{user.id}",
    )


def mark_issued(db: Session, transmittal: Transmittal) -> None:
    """Called by the issue path: the sheet lands on document control's desk."""
    transmittal.stage = str(TransmittalStage.DCC)


def forward_to_management(
    db: Session,
    *,
    transmittal: Transmittal,
    actor: User,
    to_user_id: uuid.UUID | None = None,
    note: str | None = None,
    due_days: int = 7,
) -> TransmittalReferral:
    """Document control hands the controlled sheet to project/engineering management."""
    if transmittal.stage not in (TransmittalStage.DCC, TransmittalStage.MANAGEMENT):
        raise RoutingError("این برگه در مرحله کنترل اسناد نیست؛ ارجاع به مدیریت امکان‌پذیر نیست.")

    recipient: User | None = None
    if to_user_id is not None:
        recipient = db.get(User, to_user_id)
        if recipient is None:
            raise RoutingError("کاربر مقصد یافت نشد.")
    else:
        recipient = _project_manager_of(db, transmittal.project_id, ProjectRole.ENGINEERING_MANAGER) or (
            _project_manager_of(db, transmittal.project_id, ProjectRole.PROJECT_MANAGER)
        )
    if recipient is None:
        raise RoutingError("مدیر مهندسی یا مدیر پروژه برای این پروژه تعریف نشده است.")

    _close_pending(db, transmittal, response="به مدیریت ارجاع شد")

    referral = TransmittalReferral(
        transmittal_id=transmittal.id,
        from_user_id=actor.id,
        to_user_id=recipient.id,
        action=str(ActionRequired.REVIEW),
        note=note,
        due_date=date.today() + timedelta(days=due_days),
        status=str(ReferralStatus.PENDING),
    )
    db.add(referral)
    db.flush()
    transmittal.stage = str(TransmittalStage.MANAGEMENT)
    _notify(
        db,
        transmittal=transmittal,
        user=recipient,
        template="transmittal.referred",
        action=ACTION_LABELS_FA[ActionRequired.REVIEW],
        note=note,
        actor=actor,
        referral=referral,
    )
    audit.record(
        db,
        action="transmittal.forward",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=actor,
        project_id=transmittal.project_id,
        after={"to": recipient.email, "note": note},
    )
    return referral


def refer_to_discipline(
    db: Session,
    *,
    transmittal: Transmittal,
    actor: User,
    discipline_id: uuid.UUID | None = None,
    to_user_id: uuid.UUID | None = None,
    action: ActionRequired = ActionRequired.REVIEW,
    note: str | None = None,
    due_days: int = 14,
) -> TransmittalReferral:
    """Management sends the sheet to a discipline engineer (or to a named user)."""
    if transmittal.stage not in (TransmittalStage.MANAGEMENT, TransmittalStage.DISCIPLINE):
        raise RoutingError("ارجاع به دیسیپلین فقط پس از کنترل و ارجاع به مدیریت ممکن است.")

    discipline: Discipline | None = None
    if discipline_id is not None:
        discipline = db.get(Discipline, discipline_id)
        if discipline is None or discipline.project_id != transmittal.project_id:
            raise RoutingError("دیسیپلین انتخاب‌شده به این پروژه تعلق ندارد.")

    recipient: User | None = None
    if to_user_id is not None:
        recipient = db.get(User, to_user_id)
        if recipient is None:
            raise RoutingError("کاربر مقصد یافت نشد.")
    elif discipline is not None:
        recipient = engineer_of_discipline(db, transmittal.project_id, discipline.id)
        if recipient is None:
            raise RoutingError(
                f"برای دیسیپلین «{discipline.name}» کارشناس تعریف نشده است؛ "
                "ابتدا کاربر کارشناس را به این دیسیپلین تخصیص دهید."
            )
    else:
        raise RoutingError("برای ارجاع، یک دیسیپلین یا کاربر مقصد انتخاب کنید.")

    _close_pending(db, transmittal, response="ارجاع مجدد به دیسیپلین")

    referral = TransmittalReferral(
        transmittal_id=transmittal.id,
        from_user_id=actor.id,
        to_user_id=recipient.id,
        discipline_id=discipline.id if discipline else None,
        action=str(action),
        note=note,
        due_date=date.today() + timedelta(days=due_days),
        status=str(ReferralStatus.PENDING),
    )
    db.add(referral)
    db.flush()
    transmittal.stage = str(TransmittalStage.DISCIPLINE)
    _notify(
        db,
        transmittal=transmittal,
        user=recipient,
        template="transmittal.referred",
        action=ACTION_LABELS_FA.get(action, str(action)),
        note=note,
        actor=actor,
        referral=referral,
    )
    audit.record(
        db,
        action="transmittal.refer",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=actor,
        project_id=transmittal.project_id,
        after={"to": recipient.email, "discipline": discipline.code if discipline else None},
    )
    return referral


def _close_pending(db: Session, transmittal: Transmittal, *, response: str) -> None:
    for open_referral in transmittal.referrals:
        if open_referral.status == ReferralStatus.PENDING:
            open_referral.status = str(ReferralStatus.CANCELED)
            open_referral.responded_at = datetime.now(timezone.utc)
            open_referral.response_note = response


def complete_referral(
    db: Session,
    *,
    transmittal: Transmittal,
    referral: TransmittalReferral,
    actor: User,
    note: str | None = None,
) -> TransmittalReferral:
    """The engineer reports back; the sheet returns to management."""
    if referral.status != ReferralStatus.PENDING:
        raise RoutingError("این ارجاع قبلاً بسته شده است.")
    referral.status = str(ReferralStatus.DONE)
    referral.responded_at = datetime.now(timezone.utc)
    referral.response_note = note
    transmittal.stage = str(TransmittalStage.MANAGEMENT)

    manager = _project_manager_of(db, transmittal.project_id, ProjectRole.ENGINEERING_MANAGER) or (
        _project_manager_of(db, transmittal.project_id, ProjectRole.PROJECT_MANAGER)
    )
    if manager is not None:
        notification.queue(
            db,
            user=manager,
            template="transmittal.reviewed",
            context={
                "number": transmittal.number or "پیش‌نویس",
                "by": actor.full_name,
                "note": note or "—",
                "link": f"/transmittals/{transmittal.id}",
            },
            project_id=transmittal.project_id,
            entity_type="transmittal",
            entity_id=str(transmittal.id),
            dedupe_key=f"referral-done:{referral.id}",
        )
    audit.record(
        db,
        action="transmittal.referral_complete",
        entity_type="transmittal",
        entity_id=transmittal.id,
        actor=actor,
        project_id=transmittal.project_id,
        after={"note": note},
    )
    return referral


def pending_for_user(db: Session, project_id: uuid.UUID, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Transmittal ids waiting on this user (their inbox, not the whole project's)."""
    rows = db.execute(
        select(TransmittalReferral.transmittal_id).where(
            TransmittalReferral.to_user_id == user_id,
            TransmittalReferral.status == ReferralStatus.PENDING,
        )
    ).scalars()
    return list(dict.fromkeys(rows))
