"""Processing a committed upload session: extract -> match -> decide -> persist.

This is the worker-side orchestrator.  It is idempotent per sheet: re-running it
after a crash rebuilds ``extraction_results`` for the same sheet rather than
duplicating rows.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.extraction.pipeline import ExtractionPipeline, PipelineResult
from app.models import (
    ExtractionField,
    ExtractionResult,
    ExtractionRun,
    MDLItem,
    Project,
    Sheet,
    SourceFile,
    TitleBlockTemplate,
    UploadSession,
    User,
    ValidationIssue,
)
from app.models.enums import (
    Decision,
    ExtractionSource,
    ExtractionStatus,
    FileStatus,
    IssueStatus,
    ReviewStatus,
    Severity,
    UploadStatus,
)
from app.services import notification, pdf_utils
from app.services.code_matching import CodeMatcher
from app.services.decision_engine import DocumentFacts, Issue, decide
from app.services.project_settings import merge_settings
from app.services.storage import build_key, get_storage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SheetOutcome:
    sheet_id: uuid.UUID
    result_id: uuid.UUID | None
    decision: str
    doc_code: str | None
    rev: str | None
    issues: list[str] = field(default_factory=list)


def _analyze_file(db: Session, source_file: SourceFile, local_path: str) -> int:
    """Create Sheet rows from the PDF structure (idempotent)."""
    info = pdf_utils.inspect_pdf(local_path)
    source_file.page_count = info.page_count
    source_file.is_encrypted = info.is_encrypted
    source_file.is_scanned = info.is_scanned

    existing = {sheet.page_no for sheet in source_file.sheets}
    for page in info.pages:
        if page.page_no in existing:
            sheet = next(s for s in source_file.sheets if s.page_no == page.page_no)
            sheet.width_pt, sheet.height_pt = page.width_pt, page.height_pt
            sheet.has_text_layer = page.has_text
            continue
        db.add(
            Sheet(
                source_file_id=source_file.id,
                project_id=source_file.project_id,
                page_no=page.page_no,
                width_pt=page.width_pt,
                height_pt=page.height_pt,
                has_text_layer=page.has_text,
                status="pending",
            )
        )
    db.flush()
    return info.page_count


def _persist_evidence(
    db: Session,
    *,
    run: ExtractionRun,
    result: ExtractionResult,
    pipeline_result: PipelineResult,
) -> None:
    for name, candidate in pipeline_result.fields.items():
        db.add(
            ExtractionField(
                run_id=run.id,
                field_name=name,
                raw_value=candidate.value,
                normalized_value=candidate.value,
                confidence=candidate.confidence,
                source=candidate.source,
                evidence={
                    "bbox": candidate.bbox,
                    "page_no": candidate.page_no,
                    "note": candidate.note,
                },
            )
        )
    result.payload = {
        **(pipeline_result.as_payload()),
        "decision_context": {
            "overall_confidence": result.overall_confidence,
            "match_score": result.match_score,
            "match_method": result.match_method,
        },
    }


def process_session(db: Session, session_id: uuid.UUID) -> dict:
    """Run the whole pipeline for one upload session. Returns a summary dict."""
    session = db.get(UploadSession, session_id)
    if session is None:
        return {"error": "session_not_found"}

    project = db.get(Project, session.project_id)
    if project is None:
        return {"error": "project_not_found"}

    cfg = merge_settings(project.settings)
    templates = list(
        db.execute(
            select(TitleBlockTemplate).where(
                TitleBlockTemplate.project_id == project.id, TitleBlockTemplate.is_active.is_(True)
            )
        ).scalars()
    )
    pipeline = ExtractionPipeline(project_settings=cfg, templates=templates)
    matcher = CodeMatcher(db, project.id, threshold=cfg["thresholds"]["code_similarity"])
    storage = get_storage()

    outcomes: list[SheetOutcome] = []
    seen_codes: Counter[str] = Counter()

    for source_file in session.files:
        if source_file.status in {FileStatus.PROCESSED, FileStatus.QUARANTINED, FileStatus.REJECTED}:
            continue
        if source_file.status is FileStatus.QUARANTINED:
            continue
        local_path = os.path.join(tempfile.gettempdir(), f"{source_file.id}.pdf")
        try:
            storage.download_to(source_file.storage_key, local_path)
            _analyze_file(db, source_file, local_path)
        except Exception as exc:  # noqa: BLE001 - file level failure must not stop the batch
            logger.warning("File %s could not be opened: %s", source_file.id, exc)
            source_file.status = FileStatus.FAILED
            source_file.error_message = str(exc)[:500]
            db.flush()
            if os.path.exists(local_path):
                os.unlink(local_path)
            continue

        source_file.status = FileStatus.PROCESSING
        db.flush()
        # _analyze_file() appended Sheet rows straight to the session; the already
        # loaded collection on the ORM instance is stale, so expire it before iterating.
        db.expire(source_file, ["sheets"])

        for sheet in source_file.sheets:
            outcome, seen_codes = _process_sheet(
                db,
                project=project,
                session=session,
                source_file=source_file,
                sheet=sheet,
                local_path=local_path,
                pipeline=pipeline,
                matcher=matcher,
                cfg=cfg,
                storage=storage,
                seen_codes=seen_codes,
            )
            outcomes.append(outcome)

        if os.path.exists(local_path):
            os.unlink(local_path)

        file_sheet_ids = {s.id for s in source_file.sheets}
        decisions = [o.decision for o in outcomes if o.sheet_id in file_sheet_ids]
        if decisions and all(d == Decision.RED.value for d in decisions):
            source_file.status = FileStatus.REJECTED
        elif decisions:
            source_file.status = FileStatus.PROCESSED
        else:
            # Nothing was extracted: keep the file retryable instead of pretending success.
            source_file.status = FileStatus.FAILED
            source_file.error_message = "هیچ شیت قابل پردازشی برای این فایل تولید نشد."
        db.flush()

    session.status = UploadStatus.COMPLETED
    session.stats = {
        **(session.stats or {}),
        "sheets": len(outcomes),
        "green": sum(1 for o in outcomes if o.decision == Decision.GREEN.value),
        "yellow": sum(1 for o in outcomes if o.decision == Decision.YELLOW.value),
        "red": sum(1 for o in outcomes if o.decision == Decision.RED.value),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    db.flush()

    _notify_outcome(db, project, session, outcomes)
    return {
        "session_id": str(session_id),
        "sheets": len(outcomes),
        "decisions": dict(Counter(o.decision for o in outcomes)),
        "results": [str(o.result_id) for o in outcomes if o.result_id],
    }


def _process_sheet(
    db: Session,
    *,
    project: Project,
    session: UploadSession,
    source_file: SourceFile,
    sheet: Sheet,
    local_path: str,
    pipeline: ExtractionPipeline,
    matcher: CodeMatcher,
    cfg: dict,
    storage,
    seen_codes: Counter,
) -> tuple[SheetOutcome, Counter]:
    started = datetime.now(timezone.utc)

    existing = db.execute(
        select(ExtractionResult).where(
            ExtractionResult.sheet_id == sheet.id,
            ExtractionResult.review_status.in_([ReviewStatus.PENDING]),
        )
    ).scalar_one_or_none()

    run = ExtractionRun(
        sheet_id=sheet.id,
        project_id=project.id,
        pipeline_version=settings.pipeline_version,
        status=ExtractionStatus.RUNNING,
        started_at=started,
        methods_used=[],
        models={},
    )
    db.add(run)
    db.flush()

    try:
        pipeline_result = pipeline.run(local_path, page_no=sheet.page_no)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Extraction failed for sheet %s", sheet.id)
        run.status = ExtractionStatus.FAILED
        run.error = str(exc)[:1000]
        run.finished_at = datetime.now(timezone.utc)
        db.flush()
        sheet.status = "failed"
        db.flush()
        return SheetOutcome(sheet.id, None, Decision.RED.value, None, None, [str(exc)]), seen_codes

    run.status = ExtractionStatus.COMPLETED
    run.finished_at = datetime.now(timezone.utc)
    run.methods_used = pipeline_result.methods_used
    run.models = pipeline_result.models
    run.duration_ms = pipeline_result.duration_ms
    run.raw_text = (pipeline_result.raw_text or "")[:200_000]
    run.template_id = uuid.UUID(pipeline_result.template_id) if pipeline_result.template_id else None
    db.flush()

    # ----- evidence artefacts (thumbnail + title-block crop) --------------
    title_block_key = None
    thumbnail_key = None
    try:
        if pipeline_result.title_block_png:
            title_block_key = build_key(
                project.code, "crops", str(sheet.id), "titleblock.webp"
            )
            storage.put_bytes(title_block_key, pipeline_result.title_block_png, content_type="image/png")
        thumb = pdf_utils.render_png(local_path, sheet.page_no, dpi=90)
        thumbnail_key = build_key(project.code, "thumbs", str(sheet.id), "page.webp")
        storage.put_bytes(thumbnail_key, thumb, content_type="image/png")
    except Exception as exc:  # noqa: BLE001 - artefacts are best effort
        logger.warning("Could not store evidence images for sheet %s: %s", sheet.id, exc)

    sheet.thumbnail_key = thumbnail_key
    sheet.title_block_key = title_block_key
    sheet.status = "extracted"

    # ----- matching and decision -----------------------------------------
    doc_code = pipeline_result.value("doc_code")
    candidates = matcher.match(doc_code, pipeline_result.value("title"), limit=5)
    best = candidates[0] if candidates else None
    mdl_item: MDLItem | None = db.get(MDLItem, best.mdl_item_id) if best and best.method == "exact" else None
    if mdl_item is None and best is not None and best.score >= cfg["thresholds"]["code_similarity"]:
        mdl_item = db.get(MDLItem, best.mdl_item_id)

    code_key = doc_code or ""
    seen_codes[code_key] += 1
    in_session_duplicate = bool(code_key) and seen_codes[code_key] > 1

    facts = DocumentFacts(
        doc_code=doc_code,
        rev=pipeline_result.value("rev"),
        title=pipeline_result.value("title"),
        discipline_code=pipeline_result.value("discipline"),
        unit_code=pipeline_result.value("unit"),
        doc_status=pipeline_result.value("status") or pipeline_result.value("doc_status"),
        sheet_no=_to_int(pipeline_result.value("sheet_no")),
        sheet_count=_to_int(pipeline_result.value("sheet_count")),
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
        in_session_duplicate=in_session_duplicate,
        title_block_found=pipeline_result.title_block_found,
        extraction_failed=bool(pipeline_result.error),
    )

    # The same bytes may be linked by several sessions now, so pick the original
    # deterministically instead of assuming there is exactly one other row.
    duplicate_file = db.execute(
        select(SourceFile)
        .where(
            SourceFile.project_id == project.id,
            SourceFile.sha256 == source_file.sha256,
            SourceFile.id != source_file.id,
        )
        .order_by(SourceFile.created_at, SourceFile.id)
        .limit(1)
    ).scalars().first()

    outcome = decide(
        facts,
        mdl_current_rev=mdl_item.current_rev if mdl_item else None,
        mdl_title=mdl_item.title if mdl_item else None,
        mdl_discipline_code=(mdl_item.discipline.code if mdl_item and mdl_item.discipline else None),
        mdl_unit_code=(mdl_item.unit.code if mdl_item and mdl_item.unit else None),
        mdl_status=mdl_item.status if mdl_item else None,
        mdl_page_count=mdl_item.page_count if mdl_item else None,
        candidates=candidates,
        settings=cfg,
    )

    result = existing or ExtractionResult(
        project_id=project.id,
        sheet_id=sheet.id,
        source_file_id=source_file.id,
        session_id=session.id,
    )
    result.run_id = run.id
    result.doc_code = pipeline_result.value("doc_code")
    result.doc_code_norm = pipeline_result.value("doc_code")
    result.rev = pipeline_result.value("rev")
    result.title = pipeline_result.value("title")
    result.discipline_code = pipeline_result.value("discipline")
    result.unit_code = pipeline_result.value("unit")
    result.doc_status = pipeline_result.value("status") or pipeline_result.value("doc_status")
    result.sheet_no = facts.sheet_no
    result.sheet_count = facts.sheet_count
    result.overall_confidence = outcome.overall_confidence
    result.field_confidence = pipeline_result.confidence
    result.field_sources = {
        name: candidate.source.value for name, candidate in pipeline_result.fields.items()
    }
    result.decision = outcome.decision
    result.matched_mdl_item_id = uuid.UUID(outcome.matched_item_id) if outcome.matched_item_id else None
    result.match_score = outcome.match_score
    result.match_method = outcome.match_method
    result.title_block_key = title_block_key
    result.raw_text = run.raw_text
    result.duplicate_of_file_id = duplicate_file.id if duplicate_file else None
    result.review_status = ReviewStatus.PENDING
    if existing is None:
        db.add(result)
    db.flush()

    # refresh issues (idempotent re-run)
    for issue in list(result.issues):
        db.delete(issue)
    db.flush()
    for issue in outcome.issues:
        db.add(_issue_row(result, issue))

    _persist_evidence(
        db,
        run=run,
        result=result,
        pipeline_result=pipeline_result,
    )
    db.flush()

    return (
        SheetOutcome(
            sheet_id=sheet.id,
            result_id=result.id,
            decision=outcome.decision.value,
            doc_code=result.doc_code,
            rev=result.rev,
            issues=[i.rule_code for i in outcome.issues],
        ),
        seen_codes,
    )


def _issue_row(result: ExtractionResult, issue: Issue) -> ValidationIssue:
    return ValidationIssue(
        result_id=result.id,
        rule_code=issue.rule_code,
        severity=issue.severity,
        field_name=issue.field_name,
        message=issue.message,
        message_fa=issue.message_fa,
        status=IssueStatus.OPEN,
        context=issue.context,
    )


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def _notify_outcome(
    db: Session, project: Project, session: UploadSession, outcomes: list[SheetOutcome]
) -> None:
    """Tell the DCC about yellow/red items and the submitter about rejections."""
    if not outcomes:
        return
    yellow = [o for o in outcomes if o.decision == Decision.YELLOW.value]
    red = [o for o in outcomes if o.decision == Decision.RED.value]
    if not yellow and not red:
        return

    dcc_users = _users_with_role(db, project.id, "dcc")
    link = f"{settings.public_base_url}/review?session={session.id}"
    for outcome in yellow:
        for user in dcc_users:
            notification.queue(
                db,
                user=user,
                template="extraction.needs_review",
                context={
                    "doc_code": outcome.doc_code or "—",
                    "rev": outcome.rev or "—",
                    "reason": ", ".join(outcome.issues[:3]) or "امتیاز اطمینان پایین",
                    "link": link,
                },
                project_id=project.id,
                entity_type="extraction_result",
                entity_id=str(outcome.result_id),
                dedupe_key=f"review:{outcome.result_id}",
            )
    if red:
        submitter = db.get(User, session.created_by_id)
        for outcome in red:
            notification.queue(
                db,
                user=submitter,
                template="extraction.rejected",
                context={
                    "doc_code": outcome.doc_code or "—",
                    "filename": "فایل بارگذاری‌شده",
                    "reason": ", ".join(outcome.issues[:3]) or "رد خودکار",
                    "link": f"{settings.public_base_url}/uploads/{session.id}",
                },
                project_id=project.id,
                entity_type="upload_session",
                entity_id=str(session.id),
            )
    db.flush()


def _users_with_role(db: Session, project_id: uuid.UUID, role: str) -> list[User]:
    from app.models import ProjectMember

    rows = db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.role == role
        )
    ).scalars()
    return [row.user for row in rows if row.user is not None]
