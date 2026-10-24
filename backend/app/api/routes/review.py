"""DCC review queue: the human gate between AI extraction and the register."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.models import (
    DocumentRevision,
    ExtractionResult,
    MDLItem,
    Project,
    Sheet,
    SourceFile,
)
from app.models.enums import (
    Decision,
    IssueStatus,
    ProjectRole,
    PurposeOfIssue,
    ReviewStatus,
    Severity,
    TransmittalType,
)
from app.schemas.extraction import (
    BulkReviewRequest,
    ExtractionResultDetail,
    ExtractionResultOut,
    MatchCandidateOut,
    ReviewAction,
    ReviewOutcome,
)
from app.services import acl, audit, documents, notification, transmittal_service
from app.services.code_matching import CodeMatcher
from app.services.project_settings import merge_settings
from app.services.storage import get_storage

router = APIRouter(tags=["review"])


def _result_out(result: ExtractionResult) -> ExtractionResultOut:
    return ExtractionResultOut.model_validate(result)


@router.get(
    "/projects/{project_id}/review-queue",
    summary="صف بازبینی DCC",
)
def review_queue(
    project: ProjectDep,
    db: DbSession,
    user: CurrentUser,
    decision: str | None = Query(None, pattern=r"^(GREEN|YELLOW|RED|PENDING)$"),
    review_status: str = Query("pending"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> dict:
    acl.require_project(
        db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER, ProjectRole.REVIEWER]
    )
    stmt = select(ExtractionResult).where(ExtractionResult.project_id == project.id)
    if decision:
        stmt = stmt.where(ExtractionResult.decision == decision)
    if review_status != "all":
        stmt = stmt.where(ExtractionResult.review_status == review_status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(
            stmt.order_by(ExtractionResult.created_at.desc()).offset((page - 1) * size).limit(size)
        ).scalars()
    )
    files = {
        f.id: f
        for f in db.execute(
            select(SourceFile).where(SourceFile.id.in_([r.source_file_id for r in rows]))
        ).scalars()
    }
    counts = dict(
        db.execute(
            select(ExtractionResult.decision, func.count(ExtractionResult.id))
            .where(ExtractionResult.project_id == project.id)
            .where(ExtractionResult.review_status == ReviewStatus.PENDING)
            .group_by(ExtractionResult.decision)
        ).all()
    )
    return {
        "total": total,
        "counts": {str(k): v for k, v in counts.items()},
        "items": [
            {
                **_result_out(result).model_dump(),
                "file_name": files[result.source_file_id].original_filename
                if result.source_file_id in files
                else None,
            }
            for result in rows
        ],
    }


@router.get(
    "/extraction-results/{result_id}",
    response_model=ExtractionResultDetail,
    summary="جزئیات نتیجه استخراج برای صفحه بازبینی",
)
def get_result(result_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ExtractionResultDetail:
    result = db.get(ExtractionResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="نتیجه استخراج یافت نشد.")
    project = acl.require_project(db, user, result.project_id)
    source_file = db.get(SourceFile, result.source_file_id)
    sheet = db.get(Sheet, result.sheet_id)

    matcher = CodeMatcher(db, result.project_id)
    candidates = matcher.match(result.doc_code, result.title, limit=5)

    mdl_item = db.get(MDLItem, result.matched_mdl_item_id) if result.matched_mdl_item_id else None
    detail = ExtractionResultDetail.model_validate(result)
    detail.candidates = [MatchCandidateOut(**c.as_dict()) for c in candidates]
    detail.file_name = source_file.original_filename if source_file else None
    detail.mdl_title = mdl_item.title if mdl_item else None
    detail.mdl_current_rev = mdl_item.current_rev if mdl_item else None
    detail.summary_fa = (
        "اطلاعات با اطمینان بالا خوانده شد."
        if result.decision == Decision.GREEN
        else "نیازمند بررسی و تأیید کارشناس کنترل مدارک."
        if result.decision == Decision.YELLOW
        else "مدرک رد یا قرنطینه شده است."
    )
    if sheet is not None:
        try:
            storage = get_storage()
            detail.thumbnail_url = (
                storage.presigned_url(sheet.thumbnail_key) if sheet.thumbnail_key else None
            )
            detail.title_block_url = (
                storage.presigned_url(sheet.title_block_key) if sheet.title_block_key else None
            )
        except Exception:  # noqa: BLE001 - storage outage must not break review UI
            detail.thumbnail_url = None
    if source_file is not None:
        try:
            detail.file_url = get_storage().presigned_url(
                source_file.storage_key, filename=source_file.original_filename
            )
        except Exception:  # noqa: BLE001
            detail.file_url = None
    del project
    return detail


@router.post(
    "/extraction-results/{result_id}/review",
    response_model=ReviewOutcome,
    summary="ثبت تصمیم DCC (پذیرش/اصلاح/قرنطینه/رد)",
)
def review_result(
    result_id: uuid.UUID, payload: ReviewAction, db: DbSession, user: CurrentUser
) -> ReviewOutcome:
    result = db.get(ExtractionResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="نتیجه استخراج یافت نشد.")
    project = acl.require_project(
        db, user, result.project_id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER]
    )
    if result.review_status != ReviewStatus.PENDING:
        raise HTTPException(status_code=409, detail="این مورد قبلاً بررسی شده است.")

    cfg = merge_settings(project.settings)
    open_errors = [issue for issue in result.issues if issue.severity == Severity.ERROR
                   and issue.status == IssueStatus.OPEN]

    # ---- corrections (always stored as training data) ---------------------
    if payload.corrections:
        documents.apply_human_corrections(db, result, payload.corrections, user)
    if payload.rev:
        result.rev = payload.rev
    if payload.title:
        result.title = payload.title

    note = payload.notes or ""
    revision: DocumentRevision | None = None
    transmittal_ids: list[uuid.UUID] = []

    if payload.action in {"reject", "quarantine"}:
        result.review_status = ReviewStatus.REJECTED
        result.reviewed_by_id = user.id
        result.reviewed_at = datetime.now(timezone.utc)
        result.review_note = note
        if payload.action == "quarantine":
            result.decision = Decision.RED
            source_file = db.get(SourceFile, result.source_file_id)
            if source_file is not None:
                source_file.status = "quarantined"
                source_file.error_message = note or "قرنطینه توسط DCC"
        template = "extraction.quarantined" if payload.action == "quarantine" else "extraction.rejected"
        submitter_id = None
        source_file = db.get(SourceFile, result.source_file_id)
        if source_file is not None:
            submitter_id = source_file.uploaded_by_id
        from app.models import User as UserModel

        notification.queue(
            db,
            user=db.get(UserModel, submitter_id) if submitter_id else None,
            template=template,
            context={
                "doc_code": result.doc_code or "—",
                "reason": note or "دلیل توسط کارشناس کنترل مدارک ثبت نشده است.",
                "filename": source_file.original_filename if source_file else "",
                "link": f"{settings_public_url()}/uploads",
            },
            project_id=project.id,
            entity_type="extraction_result",
            entity_id=str(result.id),
        )
        audit.record(
            db, action=f"review.{payload.action}", entity_type="extraction_result",
            entity_id=result.id, actor=user, project_id=project.id,
            before={"decision": str(result.decision)}, after={"note": note},
        )
        db.commit()
        return ReviewOutcome(
            result_id=result.id, action=payload.action, decision=str(result.decision),
            message="مدرک قرنطینه شد." if payload.action == "quarantine" else "مدرک رد شد.",
        )

    if payload.action == "ignore":
        result.review_status = ReviewStatus.REJECTED
        result.reviewed_by_id = user.id
        result.review_note = note or "نادیده گرفته شد."
        db.commit()
        return ReviewOutcome(
            result_id=result.id, action="ignore", decision=str(result.decision),
            message="مورد بدون ثبت مدرک بسته شد.",
        )

    # ----------------------------------------------------------- register
    if open_errors and not payload.override_issues:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "این مدرک ایراد مسدودکننده دارد؛ برای ثبت، تیک «نادیده‌گرفتن ایرادها» را بزنید.",
                "issues": [
                    {"rule_code": i.rule_code, "message_fa": i.message_fa} for i in open_errors
                ],
            },
        )

    mdl_item_id = payload.mdl_item_id or result.matched_mdl_item_id
    if mdl_item_id is None:
        raise HTTPException(
            status_code=422, detail="ردیف MDL مشخص نشده است؛ ابتدا مدرک را در MDL انتخاب یا اضافه کنید."
        )
    mdl_item = db.get(MDLItem, mdl_item_id)
    if mdl_item is None or mdl_item.project_id != project.id:
        raise HTTPException(status_code=404, detail="ردیف MDL یافت نشد.")
    if not result.rev:
        raise HTTPException(status_code=422, detail="شماره بازنگری مشخص نیست.")

    source_file = db.get(SourceFile, result.source_file_id)
    file_hash = source_file.sha256 if source_file else f"result-{result.id}"

    try:
        revision = documents.register_revision(
            db,
            project=project,
            mdl_item=mdl_item,
            rev=result.rev,
            file_hash=file_hash,
            title=result.title,
            page_count=source_file.page_count if source_file else 1,
            actor=user,
            source_file_id=result.source_file_id,
            extraction_result_id=result.id,
            notes=note or None,
            allow_equal_revision=bool(payload.override_issues),
        )
    except documents.RevisionConflict as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    for issue in result.issues:
        if issue.status == IssueStatus.OPEN:
            issue.status = IssueStatus.RESOLVED if not payload.override_issues else IssueStatus.OVERRIDDEN
            issue.resolved_by_id = user.id
            issue.resolution_note = note or "تأیید کارشناس کنترل مدارک"
    result.review_status = (
        ReviewStatus.CORRECTED if result.human_corrections else ReviewStatus.APPROVED
    )
    result.reviewed_by_id = user.id
    result.reviewed_at = datetime.now(timezone.utc)
    result.review_note = note
    result.decision = Decision.GREEN if result.decision != Decision.GREEN else result.decision
    db.flush()

    if payload.create_transmittal:
        purpose = PurposeOfIssue(payload.purpose or "IFR")
        drafts = transmittal_service.create_draft_transmittals(
            db,
            project=project,
            revisions=[revision],
            purpose=purpose,
            transmittal_type=TransmittalType(payload.transmittal_type),
            actor=user,
            session_id=result.session_id,
            remarks=note or None,
            settings_override=cfg,
        )
        if payload.recipient_ids:
            for draft in drafts:
                existing = {r.user_id for r in draft.recipients}
                for recipient_id in payload.recipient_ids:
                    if recipient_id not in existing:
                        from app.models import TransmittalRecipient
                        from app.models.enums import ActionRequired

                        db.add(
                            TransmittalRecipient(
                                transmittal_id=draft.id,
                                user_id=recipient_id,
                                action=ActionRequired.REVIEW,
                            )
                        )
        db.flush()
        transmittal_ids = [draft.id for draft in drafts]

    audit.record(
        db, action="review.register", entity_type="extraction_result", entity_id=result.id,
        actor=user, project_id=project.id,
        after={
            "doc_code": mdl_item.doc_code,
            "rev": revision.rev,
            "revision_id": str(revision.id),
            "transmittals": [str(t) for t in transmittal_ids],
            "corrections": result.human_corrections,
            "overridden_issues": [i.rule_code for i in open_errors] if payload.override_issues else [],
        },
    )
    db.commit()

    message = f"بازنگری {revision.rev} برای مدرک {mdl_item.doc_code} ثبت شد."
    if transmittal_ids:
        message += " پیش‌نویس برگه ارسال ساخته شد."
    return ReviewOutcome(
        result_id=result.id,
        action="register",
        decision=str(result.decision),
        revision_id=revision.id,
        transmittal_ids=transmittal_ids,
        message=message,
    )


def settings_public_url() -> str:
    from app.core.config import settings

    return settings.public_base_url


@router.post(
    "/projects/{project_id}/review/bulk",
    summary="اقدام گروهی روی صف بازبینی",
)
def bulk_review(
    project: ProjectDep, payload: BulkReviewRequest, db: DbSession, user: CurrentUser
) -> dict:
    acl.require_project(db, user, project.id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER])
    processed, skipped = [], []
    for result_id in payload.result_ids:
        result = db.get(ExtractionResult, result_id)
        if result is None or result.project_id != project.id:
            skipped.append({"id": str(result_id), "reason": "not_found"})
            continue
        if result.review_status != ReviewStatus.PENDING:
            skipped.append({"id": str(result_id), "reason": "already_reviewed"})
            continue
        if payload.action in {"reject", "quarantine"}:
            result.review_status = ReviewStatus.REJECTED
            result.reviewed_by_id = user.id
            result.review_note = payload.notes
            if payload.action == "quarantine":
                result.decision = Decision.RED
            processed.append(str(result_id))
        else:
            skipped.append({"id": str(result_id), "reason": "requires_manual_registration"})
    audit.record(
        db, action="review.bulk", entity_type="extraction_result", actor=user, project_id=project.id,
        after={"action": payload.action, "count": len(processed)},
    )
    db.commit()
    return {"processed": processed, "skipped": skipped}


@router.post("/extraction-results/{result_id}/reprocess", summary="اجرای مجدد استخراج")
def reprocess(result_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    result = db.get(ExtractionResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="نتیجه یافت نشد.")
    acl.require_project(
        db, user, result.project_id, [ProjectRole.DCC, ProjectRole.PROJECT_MANAGER, ProjectRole.SYSTEM_ADMIN]
    )
    from app.workers.tasks import reprocess_result

    try:
        task = reprocess_result.delay(str(result_id))
        return {"queued": True, "task_id": getattr(task, "id", None)}
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="سرویس پردازش در دسترس نیست.") from None
