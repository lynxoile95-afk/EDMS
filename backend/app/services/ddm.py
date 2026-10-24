"""Document Distribution Matrix resolution.

A DDM rule is a *partial* match on (discipline, unit, doc_type, purpose).  Among
all matching rules the most specific one wins:

    specificity = number of non-null criteria (desc), then priority (asc)

If nothing matches, the project's catch-all rule (all criteria null) is used; if
that does not exist either, the document must be routed manually and the caller
gets ``matched=False`` — we never silently drop a document.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DDMRule, DDMRuleRecipient
from app.models.enums import ActionRequired, PurposeOfIssue
from app.services.project_settings import merge_settings


@dataclass(slots=True)
class ResolvedRecipient:
    user_id: uuid.UUID
    action: ActionRequired
    full_name: str | None = None
    email: str | None = None


@dataclass(slots=True)
class DDMResolution:
    matched: bool
    rule_id: uuid.UUID | None = None
    rule_name: str | None = None
    due_days: int = 14
    recipients: list[ResolvedRecipient] = field(default_factory=list)
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "matched": self.matched,
            "rule_id": str(self.rule_id) if self.rule_id else None,
            "rule_name": self.rule_name,
            "due_days": self.due_days,
            "recipients": [
                {"user_id": str(r.user_id), "action": str(r.action), "full_name": r.full_name, "email": r.email}
                for r in self.recipients
            ],
            "explanation": self.explanation,
        }


def _specificity(rule: DDMRule) -> int:
    return sum(
        1
        for value in (rule.discipline_id, rule.unit_id, rule.doc_type, rule.purpose)
        if value not in (None, "")
    )


def _is_valid_now(rule: DDMRule, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if rule.valid_from and rule.valid_from > now:
        return False
    if rule.valid_to and rule.valid_to < now:
        return False
    return True


def resolve(
    db: Session,
    *,
    project_id: uuid.UUID,
    discipline_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    doc_type: str | None = None,
    purpose: PurposeOfIssue | str | None = None,
    settings: dict | None = None,
) -> DDMResolution:
    """Resolve the recipient list for one document/purpose pair."""
    purpose_value = str(purpose) if purpose is not None else None
    rules = list(
        db.execute(
            select(DDMRule).where(DDMRule.project_id == project_id, DDMRule.is_active.is_(True))
        ).scalars()
    )
    cfg = merge_settings(settings)
    matching: list[DDMRule] = []
    for rule in rules:
        if not _is_valid_now(rule):
            continue
        if rule.discipline_id and rule.discipline_id != discipline_id:
            continue
        if rule.unit_id and rule.unit_id != unit_id:
            continue
        if rule.doc_type and (doc_type or "").strip().upper() != rule.doc_type.strip().upper():
            continue
        if rule.purpose and purpose_value and str(rule.purpose) != purpose_value:
            continue
        if rule.purpose and not purpose_value:
            continue
        matching.append(rule)

    if not matching:
        return DDMResolution(
            matched=False,
            due_days=int(cfg["default_due_days"]),
            explanation="هیچ قاعده‌ای در ماتریس توزیع مدارک (DDM) با این مدرک/هدف مطابقت نداشت؛ "
            "گیرندگان باید دستی تعیین شوند.",
        )

    matching.sort(key=lambda r: (-_specificity(r), r.priority, r.name))
    rule = matching[0]
    recipients = [
        ResolvedRecipient(user_id=rec.user_id, action=ActionRequired(rec.action))
        for rec in rule.recipients
    ]
    for recipient in recipients:
        user = recipient_user(recipient.user_id, rule)
        if user is not None:
            recipient.full_name = user.full_name
            recipient.email = user.email

    return DDMResolution(
        matched=bool(recipients),
        rule_id=rule.id,
        rule_name=rule.name,
        due_days=rule.due_days or int(cfg["default_due_days"]),
        recipients=recipients,
        explanation=(
            f"قاعده «{rule.name}» با {_specificity(rule)} معیار منطبق انتخاب شد "
            f"({len(recipients)} گیرنده)."
        ),
    )


def recipient_user(user_id: uuid.UUID, rule: DDMRule):
    """Rule recipients are eagerly loaded with their user via relationship."""
    for rec in getattr(rule, "recipients", []) or []:
        if rec.user_id == user_id:
            return getattr(rec, "user", None)
    return None


def replace_recipients(db: Session, rule: DDMRule, recipients: list[dict]) -> DDMRule:
    """Set the recipients of a rule (``[{"user_id":..., "action":"review"}]``)."""
    db.execute(
        DDMRuleRecipient.__table__.delete().where(DDMRuleRecipient.rule_id == rule.id)
    )
    db.flush()
    seen: set[uuid.UUID] = set()
    for item in recipients:
        user_id = uuid.UUID(str(item["user_id"]))
        if user_id in seen:
            continue
        seen.add(user_id)
        db.add(
            DDMRuleRecipient(
                rule_id=rule.id,
                user_id=user_id,
                action=ActionRequired(item.get("action", ActionRequired.REVIEW)),
            )
        )
    db.flush()
    db.refresh(rule)
    return rule


def add_working_days(start: date, days: int, weekend: tuple[int, ...] = (3, 4), holidays: list[date] | None = None) -> date:
    """Add working days skipping Iranian weekend (Thu/Fri) and explicit holidays."""
    holidays_set = set(holidays or [])
    current = start
    added = 0
    while added < max(days, 0):
        current = date.fromordinal(current.toordinal() + 1)
        if current.weekday() in weekend or current in holidays_set:
            continue
        added += 1
    return current
