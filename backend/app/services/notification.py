"""Notification fan-out: in-app inbox, SMTP e-mail, pluggable SMS.

Rows are written to ``notification_logs`` first (transactional outbox style) and
delivered afterwards by the worker, so a mail outage never rolls back a business
operation.  Every provider is a class with a single ``send`` method — swapping in
Kavenegar/SES/Twilio is a one-line change in ``get_sms_provider``.
"""

from __future__ import annotations

import logging
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import NotificationLog, Transmittal, TransmittalRecipient, User
from app.models.enums import NotificationChannel, NotificationStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Templates (Persian, with Jalali dates supplied by the caller)
# ---------------------------------------------------------------------------
TEMPLATES: dict[str, dict[str, str]] = {
    "transmittal.issued": {
        "subject": "برگه ارسال مدارک {number} — {project}",
        "body": (
            "با سلام،\n\n"
            "برگه ارسال {number} با هدف «{purpose_fa}» در پروژه {project} برای شما صادر شد.\n"
            "تعداد مدارک: {count}\nمهلت پاسخ: {due_date}\n\n"
            "برای مشاهده و ثبت نظر خود وارد سامانه شوید:\n{link}\n\n"
            "این پیام به‌صورت خودکار ارسال شده است."
        ),
    },
    "transmittal.reminder": {
        "subject": "یادآوری مهلت پاسخ — برگه ارسال {number}",
        "body": (
            "با سلام،\n\n"
            "مهلت پاسخ برگه ارسال {number} در تاریخ {due_date} است و هنوز پاسخ شما ثبت نشده است.\n"
            "لطفاً در سامانه اقدام کنید:\n{link}\n"
        ),
    },
    "transmittal.acknowledged": {
        "subject": "تأیید دریافت برگه ارسال {number}",
        "body": "{name} دریافت برگه ارسال {number} را تأیید کرد.\n{link}\n",
    },
    "extraction.needs_review": {
        "subject": "مدرک نیازمند بازبینی است — {doc_code}",
        "body": (
            "با سلام،\n\n"
            "نتیجه استخراج خودکار مدرک {doc_code} (Revision {rev}) در صف بازبینی قرار گرفت.\n"
            "دلیل: {reason}\n\n"
            "بازبینی در سامانه:\n{link}\n"
        ),
    },
    "extraction.rejected": {
        "subject": "مدرک رد شد — {doc_code}",
        "body": "مدرک شما ({filename}) پذیرفته نشد.\nدلیل: {reason}\n\n{link}\n",
    },
    "extraction.quarantined": {
        "subject": "مدرک خارج از MDL — {doc_code}",
        "body": (
            "مدرک {doc_code} در فهرست مدارک پروژه (MDL) یافت نشد و قرنطینه شد.\n"
            "دلیل: {reason}\n\n{link}\n"
        ),
    },
    "comment.added": {
        "subject": "نظر جدید روی مدرک {doc_code}",
        "body": "{author} روی مدرک {doc_code} (Revision {rev}) نظر ثبت کرد:\n\n{text}\n\n{link}\n",
    },
    "comment.answered": {
        "subject": "پاسخ به نظر شما روی مدرک {doc_code}",
        "body": "پاسخ ثبت‌شده روی مدرک {doc_code}:\n\n{text}\n\n{link}\n",
    },
}


def render_template(name: str, context: dict) -> tuple[str, str]:
    template = TEMPLATES.get(name)
    if template is None:
        return name, str(context)
    values = {**context}
    values.setdefault("link", settings.public_base_url)
    try:
        return template["subject"].format(**values), template["body"].format(**values)
    except KeyError as exc:  # a missing placeholder must not break delivery
        logger.warning("Template %s missing placeholder %s", name, exc)
        return template["subject"], template["body"]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class EmailProvider(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SMTPEmailProvider:
    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)


class ConsoleEmailProvider:
    """Development sink: logs instead of sending."""

    def send(self, to: str, subject: str, body: str) -> None:
        logger.info("[email→%s] %s\n%s", to, subject, body)


class ConsoleSMSProvider:
    def send(self, to: str, body: str) -> None:
        logger.info("[sms→%s] %s", to, body)


class KavenegarSMSProvider:  # pragma: no cover - requires network + key
    def send(self, to: str, body: str) -> None:
        import httpx

        response = httpx.get(
            f"https://api.kavenegar.com/v1/{settings.sms_api_key}/sms/send.json",
            params={"receptor": to, "message": body},
            timeout=20,
        )
        response.raise_for_status()


def get_email_provider() -> EmailProvider:
    if settings.smtp_host and settings.smtp_host not in {"", "console"}:
        return SMTPEmailProvider()
    return ConsoleEmailProvider()


def get_sms_provider():
    if settings.sms_provider == "kavenegar" and settings.sms_api_key:
        return KavenegarSMSProvider()
    return ConsoleSMSProvider()


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------
def queue(
    db: Session,
    *,
    user: User | None,
    template: str,
    context: dict,
    channels: list[NotificationChannel] | None = None,
    project_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    dedupe_key: str | None = None,
) -> list[NotificationLog]:
    """Persist notification rows; delivery happens in the worker."""
    if user is None:
        return []
    channels = channels or [NotificationChannel.IN_APP, NotificationChannel.EMAIL]
    subject, body = render_template(template, context)
    created: list[NotificationLog] = []
    for channel in channels:
        if dedupe_key:
            existing = db.execute(
                select(NotificationLog).where(
                    NotificationLog.dedupe_key == f"{dedupe_key}:{channel}"
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
        log = NotificationLog(
            project_id=project_id,
            user_id=user.id,
            channel=NotificationChannel(channel),
            template=template,
            subject=subject,
            body=body,
            payload=context,
            status=NotificationStatus.SENT if channel == NotificationChannel.IN_APP else NotificationStatus.PENDING,
            sent_at=datetime.now(timezone.utc) if channel == NotificationChannel.IN_APP else None,
            entity_type=entity_type,
            entity_id=entity_id,
            dedupe_key=f"{dedupe_key}:{channel}" if dedupe_key else None,
        )
        db.add(log)
        created.append(log)
    db.flush()
    return created


def queue_transmittal_issued(db: Session, transmittal: Transmittal) -> list[NotificationLog]:
    purpose_fa = {
        "IFR": "ارسال برای بررسی",
        "IFA": "ارسال برای تأیید",
        "IFC": "ارسال برای اجرا",
        "IFI": "ارسال برای اطلاع",
        "RTC": "پاسخ به نظرات",
        "ASB": "چون‌ساخت",
    }.get(str(transmittal.purpose), str(transmittal.purpose))
    link = f"{settings.public_base_url}/transmittals/{transmittal.id}"
    created: list[NotificationLog] = []
    for recipient in transmittal.recipients:
        context = {
            "number": transmittal.number,
            "project": transmittal.meta.get("project_name", ""),
            "purpose_fa": purpose_fa,
            "count": len(transmittal.lines),
            "due_date": transmittal.meta.get("due_date_jalali", "—"),
            "link": link,
            "action": str(recipient.action),
        }
        created.extend(
            queue(
                db,
                user=recipient.user,
                template="transmittal.issued",
                context=context,
                project_id=transmittal.project_id,
                entity_type="transmittal",
                entity_id=str(transmittal.id),
                dedupe_key=f"trn-issued:{transmittal.id}:{recipient.user_id}",
            )
        )
    return created


def deliver(db: Session, log: NotificationLog) -> NotificationLog:
    """Attempt delivery of one queued notification (called by the worker)."""
    if log.channel == NotificationChannel.IN_APP:
        log.status = NotificationStatus.SENT
        log.sent_at = log.sent_at or datetime.now(timezone.utc)
        return log
    user = db.get(User, log.user_id) if log.user_id else None
    if user is None:
        log.status = NotificationStatus.FAILED
        log.error = "recipient not found"
        return log
    try:
        if log.channel == NotificationChannel.EMAIL:
            get_email_provider().send(user.email, log.subject or "", log.body or "")
        elif log.channel == NotificationChannel.SMS:
            get_sms_provider().send(user.email, (log.body or "")[:300])
        log.status = NotificationStatus.SENT
        log.sent_at = datetime.now(timezone.utc)
        log.error = None
    except Exception as exc:  # pragma: no cover - transport dependent
        log.status = NotificationStatus.FAILED
        log.error = str(exc)[:500]
        logger.warning("Notification %s failed: %s", log.id, exc)
    return log


def pending(db: Session, limit: int = 100) -> list[NotificationLog]:
    return list(
        db.execute(
            select(NotificationLog)
            .where(NotificationLog.status == NotificationStatus.PENDING)
            .order_by(NotificationLog.created_at)
            .limit(limit)
        ).scalars()
    )


def recipients_for(db: Session, transmittal_id: uuid.UUID) -> list[TransmittalRecipient]:
    return list(
        db.execute(
            select(TransmittalRecipient).where(TransmittalRecipient.transmittal_id == transmittal_id)
        ).scalars()
    )
