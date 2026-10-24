"""MDL import wizard, listing, editing and export."""

from __future__ import annotations

import json
import os
import tempfile
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.models import DocumentRevision, Discipline, MDLImportBatch, MDLItem, ProjectMember, Unit
from app.models.enums import ProjectRole, Severity
from app.schemas.mdl import (
    MDLItemCreate,
    MDLImportBatchOut,
    MDLImportRequest,
    MDLImportReport,
    MDLItemOut,
    MDLItemUpdate,
    MDLPreviewOut,
)
from app.services import acl, audit, mdl_import
from app.services.code_structure import (
    describe_mismatch,
    discipline_name,
    parse_code,
    resolve_discipline_code,
    structure_description,
)
from app.services.project_settings import merge_settings
from app.services.normalization import is_valid_code, normalize_code, normalize_title
from app.services.revisions import rev_to_seq

router = APIRouter(prefix="/projects/{project_id}/mdl", tags=["mdl"])


def _item_out(item: MDLItem, revision_count: int = 0) -> MDLItemOut:
    return MDLItemOut(
        id=item.id,
        project_id=item.project_id,
        doc_code=item.doc_code,
        title=item.title,
        doc_type=item.doc_type,
        current_rev=item.current_rev,
        rev_seq=item.rev_seq,
        status=item.status,
        weight=item.weight,
        page_count=item.page_count,
        due_date=item.due_date,
        planned_issue_date=item.planned_issue_date,
        is_active=item.is_active,
        discipline_id=item.discipline_id,
        unit_id=item.unit_id,
        discipline_code=item.discipline.code if item.discipline else None,
        unit_code=item.unit.code if item.unit else None,
        revision_count=revision_count,
        phase=item.phase,
        serial_no=item.serial_no,
        category=item.category,
        doc_class=item.doc_class,
        basic_detail=item.basic_detail,
        poi=item.poi,
        remark=item.remark,
    )


async def _save_temp(upload: UploadFile) -> str:
    suffix = os.path.splitext(upload.filename or "mdl.xlsx")[1] or ".xlsx"
    handle, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as out:
            while chunk := await upload.read(1024 * 1024):
                out.write(chunk)
    except Exception:
        os.unlink(path)
        raise
    return path


@router.post("/preview", response_model=MDLPreviewOut, summary="پیش‌نمایش فایل MDL و پیشنهاد نگاشت ستون‌ها")
async def preview_mdl(
    project: ProjectDep,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
    sheet_name: str | None = Form(None),
) -> MDLPreviewOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC])
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422, detail="فقط فایل اکسل (xlsx) پذیرفته می‌شود.")
    path = await _save_temp(file)
    try:
        data = mdl_import.preview(path, sheet_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"خواندن فایل ممکن نشد: {exc}") from exc
    finally:
        os.unlink(path)
    return MDLPreviewOut(**data)


@router.post("/import", response_model=MDLImportReport, summary="ایمپورت MDL با گزارش کامل اعتبارسنجی")
async def import_mdl(
    project: ProjectDep,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
    config: str = Form(..., description="JSON مطابق MDLImportRequest"),
) -> MDLImportReport:
    acl.require_project(db, user, project.id, [ProjectRole.DCC])
    try:
        request = MDLImportRequest(**json.loads(config))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"تنظیمات ایمپورت نامعتبر است: {exc}") from exc
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422, detail="فقط فایل اکسل (xlsx) پذیرفته می‌شود.")

    path = await _save_temp(file)
    try:
        report = mdl_import.import_mdl(
            db,
            project=project,
            file_path=path,
            filename=file.filename or "mdl.xlsx",
            mapping=request.mapping,
            actor=user,
            sheet_name=request.sheet_name,
            header_row=request.header_row,
            auto_create_lookups=request.auto_create_lookups,
            dry_run=request.dry_run,
        )
        if request.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        os.unlink(path)
    return MDLImportReport(**report.as_dict())


@router.get("/items", summary="فهرست مدارک MDL با فیلتر و صفحه‌بندی")
def list_items(
    project: ProjectDep,
    db: DbSession,
    q: str | None = Query(None, description="جست‌وجو در کد یا عنوان"),
    discipline: str | None = None,
    status: str | None = None,
    has_revision: bool | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
) -> dict:
    stmt = select(MDLItem).where(MDLItem.project_id == project.id)
    if q:
        term = f"%{normalize_code(q)}%" if any(ch.isdigit() for ch in q) else f"%{q.strip()}%"
        stmt = stmt.where(or_(MDLItem.doc_code_norm.like(term), MDLItem.title.like(f"%{q.strip()}%")))
    if discipline:
        stmt = stmt.join(Discipline, MDLItem.discipline_id == Discipline.id).where(
            Discipline.code == discipline.upper()
        )
    if status:
        stmt = stmt.where(MDLItem.status == status)
    if has_revision is not None:
        subquery = select(DocumentRevision.mdl_item_id).where(DocumentRevision.project_id == project.id)
        stmt = stmt.where(MDLItem.id.in_(subquery) if has_revision else MDLItem.id.not_in(subquery))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = list(
        db.execute(
            stmt.order_by(MDLItem.doc_code_norm).offset((page - 1) * size).limit(size)
        ).scalars()
    )
    counts = dict(
        db.execute(
            select(DocumentRevision.mdl_item_id, func.count(DocumentRevision.id))
            .where(DocumentRevision.project_id == project.id)
            .group_by(DocumentRevision.mdl_item_id)
        ).all()
    )
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_item_out(item, counts.get(item.id, 0)).model_dump() for item in items],
    }


@router.patch("/items/{item_id}", response_model=MDLItemOut, summary="ویرایش ردیف MDL (با ثبت دلیل)")
def update_item(
    project: ProjectDep,
    item_id: uuid.UUID,
    payload: MDLItemUpdate,
    db: DbSession,
    user: CurrentUser,
) -> MDLItemOut:
    acl.require_project(db, user, project.id, [ProjectRole.DCC])
    item = db.get(MDLItem, item_id)
    if item is None or item.project_id != project.id:
        raise HTTPException(status_code=404, detail="ردیف MDL یافت نشد.")
    before = {
        "title": item.title, "current_rev": item.current_rev, "status": item.status,
        "doc_type": item.doc_type, "weight": item.weight,
    }
    updates = payload.model_dump(exclude_unset=True, exclude={"reason", "discipline_code", "unit_code"})
    for field, value in updates.items():
        setattr(item, field, value)
    if payload.title:
        item.title_norm = normalize_title(payload.title)
    if payload.current_rev:
        item.rev_seq = rev_to_seq(payload.current_rev, "auto")
    if payload.discipline_code:
        discipline = db.execute(
            select(Discipline).where(
                Discipline.project_id == project.id, Discipline.code == payload.discipline_code.upper()
            )
        ).scalar_one_or_none()
        if discipline:
            item.discipline_id = discipline.id
    if payload.unit_code:
        unit = db.execute(
            select(Unit).where(Unit.project_id == project.id, Unit.code == payload.unit_code.upper())
        ).scalar_one_or_none()
        if unit:
            item.unit_id = unit.id
    if payload.aliases is not None:
        item.aliases = payload.aliases
    audit.record(
        db, action="mdl.item.update", entity_type="mdl_item", entity_id=item.id, actor=user,
        project_id=project.id, before=before,
        after={**updates, "reason": payload.reason},
    )
    db.commit()
    db.refresh(item)
    return _item_out(item)


@router.post(
    "/items",
    response_model=MDLItemOut,
    status_code=201,
    summary="افزودن دستی یک ردیف MDL (فقط کارشناس کنترل اسناد)",
)
def create_item(
    project: ProjectDep,
    payload: MDLItemCreate,
    db: DbSession,
    user: CurrentUser,
) -> MDLItemOut:
    """The manual door into the MDL, beside the Excel import.

    Same rules as the import: the code must be unique inside the project and the
    discipline/unit codes must already exist, so a hand-typed row can never point at
    a lookup the project does not have.
    """
    acl.require_project(db, user, project.id, [ProjectRole.DCC])
    doc_code = payload.doc_code.strip().upper()
    cfg = merge_settings(project.settings)
    if not is_valid_code(normalize_code(doc_code), cfg["doc_code_pattern"]):
        raise HTTPException(status_code=422, detail=describe_mismatch(doc_code))
    parsed = parse_code(doc_code, cfg["doc_code_pattern"])
    duplicate = db.execute(
        select(MDLItem).where(MDLItem.project_id == project.id, MDLItem.doc_code_norm == normalize_code(doc_code))
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail=f"کد مدرک «{doc_code}» قبلاً در MDL ثبت شده است.")

    phase = (payload.phase or "").strip().upper() or parsed.get("phase")
    serial_no = (payload.serial_no or "").strip() or parsed.get("serial")
    doc_type = (payload.doc_type or "").strip().upper() or parsed.get("type")
    unit_code = (payload.unit_code or "").strip().upper() or parsed.get("unit")
    discipline_code = (payload.discipline_code or "").strip().upper() or parsed.get("discipline")
    known_disciplines = {
        row.code.strip().upper()
        for row in db.execute(
            select(Discipline).where(Discipline.project_id == project.id)
        ).scalars()
    }
    discipline_code = resolve_discipline_code(discipline_code, known_disciplines)

    # A code typed into the form is trusted to name its own segments, and the
    # import behaves the same way: an unknown discipline/unit coming *from the code*
    # is created as a lookup. A value typed explicitly into the discipline/unit fields
    # is treated as a claim about an existing lookup and must resolve, so a typo is
    # refused instead of silently inventing "CIVL".
    discipline_id = None
    if discipline_code:
        discipline = db.execute(
            select(Discipline).where(
                Discipline.project_id == project.id,
                Discipline.code == discipline_code,
            )
        ).scalar_one_or_none()
        if discipline is None and payload.discipline_code:
            raise HTTPException(
                status_code=422, detail=f"دیسیپلین «{discipline_code}» در این پروژه تعریف نشده است."
            )
        if discipline is None:
            discipline = Discipline(
                project_id=project.id, code=discipline_code, name=discipline_name(discipline_code)
            )
            db.add(discipline)
            db.flush()
        discipline_id = discipline.id

    unit_id = None
    if unit_code:
        unit = db.execute(
            select(Unit).where(
                Unit.project_id == project.id, Unit.code == unit_code
            )
        ).scalar_one_or_none()
        if unit is None and payload.unit_code:
            raise HTTPException(
                status_code=422, detail=f"یونیت «{unit_code}» در این پروژه تعریف نشده است."
            )
        if unit is None:
            unit = Unit(project_id=project.id, code=unit_code, name=unit_code)
            db.add(unit)
            db.flush()
        unit_id = unit.id

    item = MDLItem(
        project_id=project.id,
        doc_code=doc_code,
        doc_code_norm=normalize_code(doc_code),
        title=payload.title.strip(),
        title_norm=normalize_title(payload.title.strip()),
        doc_type=doc_type,
        phase=phase,
        serial_no=serial_no,
        category=payload.category,
        doc_class=(payload.doc_class or "").strip().upper() or None,
        basic_detail=(payload.basic_detail or "").strip().upper() or None,
        poi=(payload.poi or "").strip().upper() or None,
        remark=payload.remark,
        current_rev=payload.current_rev,
        rev_seq=rev_to_seq(payload.current_rev, "auto") if payload.current_rev else 0,
        status=payload.status or "Draft",
        weight=payload.weight,
        page_count=payload.page_count,
        discipline_id=discipline_id,
        unit_id=unit_id,
    )
    db.add(item)
    db.flush()
    audit.record(
        db,
        action="mdl.item.create",
        entity_type="mdl_item",
        entity_id=item.id,
        actor=user,
        project_id=project.id,
        after={"doc_code": item.doc_code, "title": item.title, "source": "manual"},
    )
    db.commit()
    db.refresh(item)
    return _item_out(item)


@router.delete(
    "/items/{item_id}",
    status_code=204,
    summary="حذف ردیف MDL (فقط کارشناس کنترل اسناد و تنها بدون بازنگری)",
)
def delete_item(project: ProjectDep, item_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    acl.require_project(db, user, project.id, [ProjectRole.DCC])
    item = db.get(MDLItem, item_id)
    if item is None or item.project_id != project.id:
        raise HTTPException(status_code=404, detail="ردیف MDL یافت نشد.")
    revisions = db.execute(
        select(func.count(DocumentRevision.id)).where(DocumentRevision.mdl_item_id == item.id)
    ).scalar_one()
    if revisions:
        raise HTTPException(
            status_code=409,
            detail=f"این ردیف {revisions} بازنگری ثبت‌شده دارد و حذف نمی‌شود؛ ابتدا وضعیت آن را «Void» کنید.",
        )
    audit.record(
        db,
        action="mdl.item.delete",
        entity_type="mdl_item",
        entity_id=item.id,
        actor=user,
        project_id=project.id,
        before={"doc_code": item.doc_code, "title": item.title},
    )
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.get(
    "/code-structure",
    summary="ساختار کدینگ مدارک پروژه (راهنمای سگمنت‌ها)",
)
def code_structure(project: ProjectDep, db: DbSession, user: CurrentUser) -> dict:
    """The coding rule this project validates against, with an example.

    Comes straight from the project's ``doc_code_pattern`` plus the vocabulary of
    disciplines and document types seen in the customer's MDL, so the UI can explain
    the code instead of only rejecting it.
    """
    acl.require_project(db, user, project.id)
    cfg = merge_settings(project.settings)
    data = structure_description()
    data["pattern"] = cfg["doc_code_pattern"]
    data["project_code_hint"] = project.code
    data["known_disciplines"] = [
        {"id": row.id, "code": row.code, "name": row.name}
        for row in db.execute(
            select(Discipline).where(Discipline.project_id == project.id).order_by(Discipline.code)
        ).scalars()
    ]
    return data


@router.get("/import-batches", response_model=list[MDLImportBatchOut], summary="تاریخچه ایمپورت‌ها")
def list_batches(project: ProjectDep, db: DbSession) -> list[MDLImportBatchOut]:
    rows = db.execute(
        select(MDLImportBatch)
        .where(MDLImportBatch.project_id == project.id)
        .order_by(MDLImportBatch.created_at.desc())
        .limit(50)
    ).scalars()
    return [MDLImportBatchOut.model_validate(r) for r in rows]


@router.get("/export", summary="خروجی اکسل MDL")
def export_mdl(project: ProjectDep, db: DbSession) -> Response:
    items = list(
        db.execute(
            select(MDLItem).where(MDLItem.project_id == project.id).order_by(MDLItem.doc_code_norm)
        ).scalars()
    )
    payload = mdl_import.export_mdl_bytes(items)
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="MDL-{project.code}.xlsx"'},
    )


@router.get("/search", summary="جست‌وجوی فازی برای انتخاب مدرک")
def search(
    project: ProjectDep,
    db: DbSession,
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    from app.services.code_matching import CodeMatcher

    matcher = CodeMatcher(db, project.id)
    candidates = matcher.match(q, None, limit=limit)
    if candidates:
        return [c.as_dict() for c in candidates]
    like = f"%{q.strip()}%"
    rows = db.execute(
        select(MDLItem)
        .where(MDLItem.project_id == project.id, MDLItem.title.like(like))
        .limit(limit)
    ).scalars()
    return [
        {
            "mdl_item_id": str(item.id),
            "doc_code": item.doc_code,
            "title": item.title,
            "discipline_code": item.discipline.code if item.discipline else None,
            "score": 0.5,
            "method": "title",
            "reasons": ["تطبیق بر اساس عنوان"],
        }
        for item in rows
    ]
