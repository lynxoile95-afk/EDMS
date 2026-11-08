"""Project dashboards and KPI aggregates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbSession, ProjectDep
from app.models import (
    DocumentRevision,
    ExtractionResult,
    MDLItem,
    Transmittal,
    UploadSession,
    ValidationIssue,
)
from app.models.enums import Decision, ReviewStatus, RevisionStatus, TransmittalStatus
from app.schemas.transmittals import DashboardOut

router = APIRouter(prefix="/projects/{project_id}", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut, summary="داشبورد پروژه")
def dashboard(project: ProjectDep, db: DbSession) -> DashboardOut:
    documents_total = db.execute(
        select(func.count(MDLItem.id)).where(MDLItem.project_id == project.id, MDLItem.is_active.is_(True))
    ).scalar_one()
    revisions_total = db.execute(
        select(func.count(DocumentRevision.id)).where(DocumentRevision.project_id == project.id)
    ).scalar_one()
    documents_issued = db.execute(
        select(func.count(func.distinct(DocumentRevision.mdl_item_id))).where(
            DocumentRevision.project_id == project.id,
            DocumentRevision.status == RevisionStatus.ISSUED,
        )
    ).scalar_one()
    review_queue = db.execute(
        select(func.count(ExtractionResult.id)).where(
            ExtractionResult.project_id == project.id,
            ExtractionResult.review_status == ReviewStatus.PENDING,
        )
    ).scalar_one()
    quarantined = db.execute(
        select(func.count(ExtractionResult.id)).where(
            ExtractionResult.project_id == project.id,
            ExtractionResult.decision == Decision.RED,
            ExtractionResult.review_status == ReviewStatus.PENDING,
        )
    ).scalar_one()
    transmittals_total = db.execute(
        select(func.count(Transmittal.id)).where(Transmittal.project_id == project.id)
    ).scalar_one()
    transmittals_open = db.execute(
        select(func.count(Transmittal.id)).where(
            Transmittal.project_id == project.id,
            Transmittal.status.in_(
                [
                    TransmittalStatus.ISSUED,
                    TransmittalStatus.PARTIALLY_ACKNOWLEDGED,
                    TransmittalStatus.PENDING_APPROVAL,
                    TransmittalStatus.DRAFT,
                ]
            ),
        )
    ).scalar_one()

    window_start = datetime.now(timezone.utc) - timedelta(days=30)
    total_recent = db.execute(
        select(func.count(ExtractionResult.id)).where(
            ExtractionResult.project_id == project.id, ExtractionResult.created_at >= window_start
        )
    ).scalar_one()
    green_recent = db.execute(
        select(func.count(ExtractionResult.id)).where(
            ExtractionResult.project_id == project.id,
            ExtractionResult.created_at >= window_start,
            ExtractionResult.decision == Decision.GREEN,
        )
    ).scalar_one()
    auto_accept_rate = round(green_recent / total_recent, 3) if total_recent else 0.0

    rule_rows = db.execute(
        select(ValidationIssue.rule_code, func.count(ValidationIssue.id))
        .join(ExtractionResult, ValidationIssue.result_id == ExtractionResult.id)
        .where(ExtractionResult.project_id == project.id)
        .group_by(ValidationIssue.rule_code)
        .order_by(func.count(ValidationIssue.id).desc())
        .limit(10)
    ).all()

    uploads_recent = db.execute(
        select(func.count(UploadSession.id)).where(
            UploadSession.project_id == project.id,
            UploadSession.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
        )
    ).scalar_one()

    overdue = db.execute(
        select(func.count(MDLItem.id)).where(
            MDLItem.project_id == project.id,
            MDLItem.is_active.is_(True),
            MDLItem.due_date.is_not(None),
            MDLItem.due_date < date.today(),
            ~MDLItem.id.in_(
                select(DocumentRevision.mdl_item_id).where(
                    DocumentRevision.project_id == project.id,
                    DocumentRevision.status == RevisionStatus.ISSUED,
                )
            ),
        )
    ).scalar_one()

    return DashboardOut(
        project_id=project.id,
        documents_total=documents_total,
        documents_issued=documents_issued,
        revisions_total=revisions_total,
        review_queue=review_queue,
        quarantined=quarantined,
        transmittals_total=transmittals_total,
        transmittals_open=transmittals_open,
        auto_accept_rate=auto_accept_rate,
        issues_by_rule={str(code): int(count) for code, count in rule_rows},
        uploads_last_7_days=uploads_recent,
        overdue_documents=overdue,
    )
