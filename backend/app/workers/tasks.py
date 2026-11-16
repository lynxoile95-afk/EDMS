"""Celery tasks. Every task is idempotent and owns its own DB session."""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.session import session_scope
from app.models import (
    ExtractionResult,
    NotificationLog,
    Project,
    Sheet,
    SourceFile,
    Transmittal,
    TransmittalRecipient,
    UploadSession,
)
from app.models.enums import (
    Decision,
    NotificationStatus,
    ReviewStatus,
    TransmittalStatus,
)
from app.services import notification
from app.services.processing import process_session
from app.services.storage import get_storage
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="edms.ingest.process_session", bind=True, max_retries=2)
def process_upload_session(self, session_id: str) -> dict:
    """Ingest + extract + decide for a whole upload session."""
    try:
        with session_scope() as db:
            summary = process_session(db, uuid.UUID(session_id))
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.exception("process_upload_session failed for %s", session_id)
        if self.request.retries < (self.max_retries or 0):
            raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1)) from exc
        with session_scope() as db:
            session = db.get(UploadSession, uuid.UUID(session_id))
            if session is not None:
                session.status = "failed"
                session.stats = {**(session.stats or {}), "error": str(exc)[:500]}
        raise


@celery_app.task(name="edms.extract.reprocess", bind=True, max_retries=1)
def reprocess_result(self, result_id: str) -> dict:
    """Re-run extraction for a single result (new template / new model)."""
    with session_scope() as db:
        result = db.get(ExtractionResult, uuid.UUID(result_id))
        if result is None:
            return {"error": "not_found"}
        sheet = db.get(Sheet, result.sheet_id)
        source_file = db.get(SourceFile, result.source_file_id)
        if sheet is None or source_file is None:
            return {"error": "artefacts_missing"}

        storage = get_storage()
        local_path = os.path.join(tempfile.gettempdir(), f"reprocess-{sheet.id}.pdf")
        storage.download_to(source_file.storage_key, local_path)
        try:
            from app.models import ExtractionRun, TitleBlockTemplate
            from app.services.code_matching import CodeMatcher
            from app.services.decision_engine import DocumentFacts, decide
            from app.extraction.pipeline import ExtractionPipeline
            from app.services.project_settings import merge_settings

            project = db.get(Project, result.project_id)
            templates = list(
                db.execute(
                    select(TitleBlockTemplate).where(
                        TitleBlockTemplate.project_id == result.project_id,
                        TitleBlockTemplate.is_active.is_(True),
                    )
                ).scalars()
            )
            pipeline = ExtractionPipeline(project_settings=project.settings, templates=templates)
            pipeline_result = pipeline.run(local_path, page_no=sheet.page_no)
            matcher = CodeMatcher(db, result.project_id, threshold=merge_settings(project.settings)["thresholds"]["code_similarity"])
            candidates = matcher.match(pipeline_result.value("doc_code"), pipeline_result.value("title"))
            best = candidates[0] if candidates else None
            from app.models import MDLItem

            mdl_item = db.get(MDLItem, best.mdl_item_id) if best else None
            facts = DocumentFacts(
                doc_code=pipeline_result.value("doc_code"),
                rev=pipeline_result.value("rev"),
                title=pipeline_result.value("title"),
                discipline_code=pipeline_result.value("discipline"),
                unit_code=pipeline_result.value("unit"),
                doc_status=pipeline_result.value("status"),
                field_confidence={
                    "doc_code": pipeline_result.confidence.get("doc_code", 0.0),
                    "rev": pipeline_result.confidence.get("rev", 0.0),
                    "title": pipeline_result.confidence.get("title", 0.0),
                    "discipline_code": pipeline_result.confidence.get("discipline", 0.0),
                    "unit_code": pipeline_result.confidence.get("unit", 0.0),
                    "doc_status": pipeline_result.confidence.get("doc_status", 0.0),
                },
                methods_used=pipeline_result.methods_used,
                filename=source_file.original_filename,
                title_block_found=pipeline_result.title_block_found,
            )
            outcome = decide(
                facts,
                mdl_current_rev=mdl_item.current_rev if mdl_item else None,
                mdl_title=mdl_item.title if mdl_item else None,
                candidates=candidates,
                settings=project.settings,
            )
            result.doc_code = pipeline_result.value("doc_code")
            result.doc_code_norm = pipeline_result.value("doc_code")
            result.rev = pipeline_result.value("rev")
            result.title = pipeline_result.value("title")
            result.overall_confidence = outcome.overall_confidence
            result.field_confidence = pipeline_result.confidence
            result.decision = outcome.decision
            result.payload = pipeline_result.as_payload()
            return {"result_id": result_id, "decision": outcome.decision.value}
        finally:
            if os.path.exists(local_path):
                os.unlink(local_path)


@celery_app.task(name="edms.notify.deliver")
def deliver_notifications(limit: int = 200) -> dict:
    """Flush queued e-mail/SMS notifications (at-least-once, deduplicated by row)."""
    sent = failed = 0
    with session_scope() as db:
        for log in notification.pending(db, limit):
            notification.deliver(db, log)
            if log.status is NotificationStatus.SENT:
                sent += 1
            else:
                failed += 1
    return {"sent": sent, "failed": failed}


@celery_app.task(name="edms.notify.reminders")
def send_reminders() -> dict:
    """Daily reminder sweep for transmittals approaching or past their due date."""
    queued = 0
    with session_scope() as db:
        today = date.today()
        horizon = today + timedelta(days=3)
        rows = db.execute(
            select(Transmittal).where(
                Transmittal.status.in_(
                    [TransmittalStatus.ISSUED, TransmittalStatus.PARTIALLY_ACKNOWLEDGED]
                ),
                Transmittal.due_date.is_not(None),
                Transmittal.due_date <= horizon,
            )
        ).scalars()
        for transmittal in rows:
            project = db.get(Project, transmittal.project_id)
            for recipient in transmittal.recipients:
                if str(recipient.ack_status) != "pending":
                    continue
                notification.queue(
                    db,
                    user=recipient.user,
                    template="transmittal.reminder",
                    context={
                        "number": transmittal.number,
                        "due_date": transmittal.due_date.isoformat() if transmittal.due_date else "—",
                        "link": f"{settings.public_base_url}/transmittals/{transmittal.id}",
                        "project": project.name if project else "",
                    },
                    project_id=transmittal.project_id,
                    entity_type="transmittal",
                    entity_id=str(transmittal.id),
                    dedupe_key=f"remind:{transmittal.id}:{recipient.user_id}:{today.isoformat()}",
                )
                queued += 1
    return {"queued": queued}


@celery_app.task(name="edms.maintenance.cleanup")
def cleanup() -> dict:
    """Remove stale temp files and expire old read notifications."""
    removed = 0
    temp_root = tempfile.gettempdir()
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    for name in os.listdir(temp_root):
        if not (name.startswith("edms-") or name.endswith(".pdf")):
            continue
        path = os.path.join(temp_root, name)
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc) < cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    with session_scope() as db:
        old = db.execute(
            select(NotificationLog).where(
                NotificationLog.created_at < datetime.now(timezone.utc) - timedelta(days=180)
            )
        ).scalars()
        deleted = 0
        for row in old:
            db.delete(row)
            deleted += 1
    return {"temp_files_removed": removed, "notifications_pruned": deleted}


@celery_app.task(name="edms.transmittal.issue")
def issue_transmittal_task(transmittal_id: str, actor_id: str) -> dict:
    """Issue a transmittal from a background job (used for large batches)."""
    from app.models import User
    from app.services import transmittal_service

    with session_scope() as db:
        transmittal = db.get(Transmittal, uuid.UUID(transmittal_id))
        actor = db.get(User, uuid.UUID(actor_id))
        if transmittal is None or actor is None:
            return {"error": "not_found"}
        transmittal_service.issue_transmittal(
            db, transmittal=transmittal, actor=actor, storage=get_storage()
        )
        return {"transmittal_id": transmittal_id, "number": transmittal.number}
