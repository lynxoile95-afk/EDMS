"""Transmittal registration endpoints: the «ثبت و بارگذاری مدرک» screen.

Flow: ``POST /registration/extract`` (one or more PDFs) → rows appear with what the
model read → the operator edits / confirms → ``POST /registration/save`` writes the
documents, revisions and transmittal lines in one transaction.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, ProjectDep
from app.models import RegistrationRow, UploadSession
from app.models.enums import PurposeOfIssue
from app.services import acl, ingest, registration
from app.services.metadata_extraction import extract_metadata
from app.services.pdf_utils import inspect_pdf
from app.services.ingest import UploadRejected

router = APIRouter(prefix="/projects/{project_id}/registration", tags=["registration"])


# --------------------------------------------------------------------- schemas
class RowUpdate(BaseModel):
    """Only the ten columns are editable; the extraction proof is read-only."""

    doc_no: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=512)
    discipline_code: str | None = Field(default=None, max_length=32)
    version: str | None = Field(default=None, max_length=16)
    poi: str | None = Field(default=None, max_length=16)
    doc_status: str | None = Field(default=None, max_length=64)
    doc_percent: int | None = Field(default=None, ge=0, le=100)
    confidentiality: str | None = Field(default=None, max_length=24)
    confirmed: bool | None = None


class SaveRequest(BaseModel):
    row_ids: list[uuid.UUID] | None = None


class ConfirmRequest(BaseModel):
    confirmed: bool = True


# ----------------------------------------------------------------------- views
def _registration_out(db, project, transmittal, rows) -> dict:
    settings = registration.settings_for(project)
    cfg = settings["registration"]
    return {
        "id": str(transmittal.id),
        "project_id": str(project.id),
        "purpose": str(transmittal.purpose),
        "status": str(transmittal.status),
        "stage": transmittal.stage,
        "number": transmittal.number,
        "settings": {
            "doc_number_regex": cfg["doc_number_regex"],
            "revision_regex": cfg["revision_regex"],
            "confidence_threshold": cfg["confidence_threshold"],
            "discipline_labels": cfg["discipline_labels"],
        },
        "rows": [registration.row_to_dict(row) for row in rows],
    }


def _ensure_registration(db, project, user) -> tuple:
    transmittal = registration.get_registration(db, project)
    if transmittal is None:
        transmittal = registration.create_registration(db, project=project, user=user)
        db.commit()
        db.refresh(transmittal)
    rows = (
        db.query(RegistrationRow)
        .filter(RegistrationRow.transmittal_id == transmittal.id)
        .order_by(RegistrationRow.row_no)
        .all()
    )
    return transmittal, rows


@router.get("", summary="پیش‌نویس برگه ثبت مدارک و ردیف‌های آن")
def get_registration(project: ProjectDep, db: DbSession, user: CurrentUser) -> dict:
    acl.require_project(db, user, project.id)
    transmittal, rows = _ensure_registration(db, project, user)
    return _registration_out(db, project, transmittal, rows)


@router.post("/extract", summary="بارگذاری PDF و استخراج خودکار فیلدهای هر ردیف")
def extract_rows(
    project: ProjectDep,
    db: DbSession,
    user: CurrentUser,
    files: list[UploadFile] = File(...),
) -> dict:
    """Store each PDF unchanged, ask the model, and return one row per file."""
    registration.require_writer(db, user, project)
    if not files:
        raise HTTPException(status_code=422, detail="هیچ فایلی ارسال نشده است.")
    if len(files) > 20:
        raise HTTPException(status_code=422, detail="حداکثر ۲۰ فایل در هر بار بارگذاری.")

    transmittal, _ = _ensure_registration(db, project, user)
    settings = registration.settings_for(project)
    cfg = settings["registration"]

    created: list[RegistrationRow] = []
    for upload in files:
        session = db.get(UploadSession, transmittal.session_id) if transmittal.session_id else None
        if session is None:
            raise HTTPException(status_code=409, detail="جلسهٔ بارگذاری این برگه یافت نشد.")
        try:
            stored = ingest.store_file(
                db, session=session, upload=upload, user=user, project_code=project.code
            )
        except UploadRejected as exc:
            db.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        source_file = stored.source_file
        path, cleanup = registration.materialise(source_file)
        try:
            try:
                page_count = inspect_pdf(path).page_count
            except Exception:  # noqa: BLE001
                page_count = 0
            outcome = extract_metadata(
                path,
                pages=list(cfg.get("extraction_pages") or [1, 2]),
                retries=int(cfg.get("extraction_retries", 2)),
            )
        finally:
            cleanup()

        row = registration.add_row_from_extraction(
            db,
            project=project,
            transmittal=transmittal,
            source_file=source_file,
            page_count=page_count,
            outcome=outcome,
            settings=settings,
        )
        registration.validate_row(db, project=project, row=row, settings=settings)
        created.append(row)

    db.commit()
    for row in created:
        db.refresh(row)
    _, rows = _ensure_registration(db, project, user)
    return _registration_out(db, project, transmittal, rows)


@router.patch("/rows/{row_id}", summary="ویرایش یک ردیف توسط کاربر")
def update_row(
    project: ProjectDep,
    row_id: uuid.UUID,
    payload: RowUpdate,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    registration.require_writer(db, user, project)
    row = db.get(RegistrationRow, row_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status_code=404, detail="ردیف ثبت مدارک یافت نشد.")
    if row.saved:
        raise HTTPException(status_code=409, detail="این ردیف قبلاً در دفتر مدارک ثبت شده است.")

    settings = registration.settings_for(project)
    fields = payload.model_dump(exclude_unset=True, exclude={"confirmed"})
    for name, value in fields.items():
        if isinstance(value, str):
            value = value.strip() or None
        if name in {"doc_no", "version", "discipline_code", "poi"} and value:
            value = value.upper()
        setattr(row, name, value)
    if "discipline_code" in fields:
        labels = settings["registration"].get("discipline_labels") or {}
        row.discipline_label = labels.get((row.discipline_code or "").upper()) if row.discipline_code else None
    if payload.confirmed is not None:
        row.review_confirmed = payload.confirmed

    registration.validate_row(db, project=project, row=row, settings=settings)
    db.commit()
    db.refresh(row)
    return registration.row_to_dict(row)


@router.post("/rows/{row_id}/confirm", summary="تأیید ردیف نیازمند بررسی")
def confirm_row(
    project: ProjectDep, row_id: uuid.UUID, payload: ConfirmRequest, db: DbSession, user: CurrentUser
) -> dict:
    registration.require_writer(db, user, project)
    row = db.get(RegistrationRow, row_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status_code=404, detail="ردیف ثبت مدارک یافت نشد.")
    settings = registration.settings_for(project)
    verdict = registration.validate_row(db, project=project, row=row, settings=settings)
    if payload.confirmed and verdict.blocked:
        raise HTTPException(status_code=409, detail="ردیف مسدود است و قابل تأیید نیست: " + "؛ ".join(verdict.messages))
    row.review_confirmed = payload.confirmed
    db.commit()
    db.refresh(row)
    return registration.row_to_dict(row)


@router.post("/rows/{row_id}/re-extract", summary="استخراج مجدد یک ردیف")
def re_extract(project: ProjectDep, row_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    registration.require_writer(db, user, project)
    row = db.get(RegistrationRow, row_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status_code=404, detail="ردیف ثبت مدارک یافت نشد.")
    if row.saved:
        raise HTTPException(status_code=409, detail="این ردیف قبلاً ثبت شده است.")
    settings = registration.settings_for(project)
    try:
        registration.extract_for_row(db, row=row, settings=settings)
    except registration.RegistrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    db.commit()
    db.refresh(row)
    return registration.row_to_dict(row)


@router.delete("/rows/{row_id}", status_code=204, response_model=None, summary="حذف ردیف")
def delete_row(project: ProjectDep, row_id: uuid.UUID, db: DbSession, user: CurrentUser):
    from fastapi import Response

    registration.require_writer(db, user, project)
    row = db.get(RegistrationRow, row_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status_code=404, detail="ردیف ثبت مدارک یافت نشد.")
    if row.saved:
        raise HTTPException(status_code=409, detail="ردیف ثبت‌شده حذف نمی‌شود؛ بازنگری آن را باطل کنید.")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/save", summary="ثبت نهایی ردیف‌ها در دفتر مدارک")
def save(
    project: ProjectDep, db: DbSession, user: CurrentUser, payload: SaveRequest | None = None
) -> dict:
    registration.require_writer(db, user, project)
    transmittal = registration.get_registration(db, project)
    if transmittal is None:
        raise HTTPException(status_code=404, detail="برگهٔ ثبت فعالی وجود ندارد.")
    settings = registration.settings_for(project)
    try:
        result = registration.save_rows(
            db,
            project=project,
            transmittal=transmittal,
            user=user,
            settings=settings,
            row_ids=payload.row_ids if payload else None,
        )
    except registration.RegistrationError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    db.commit()
    return {
        **result,
        "transmittal_id": str(transmittal.id),
        "message": "ردیف‌ها در دفتر مدارک ثبت و به برگهٔ ارسال اضافه شدند.",
    }


@router.post("/discard", status_code=204, response_model=None, summary="دور ریختن برگهٔ ثبت")
def discard(project: ProjectDep, db: DbSession, user: CurrentUser):
    from fastapi import Response

    registration.require_writer(db, user, project)
    transmittal = registration.get_registration(db, project)
    if transmittal is not None:
        for row in db.query(RegistrationRow).filter(RegistrationRow.transmittal_id == transmittal.id):
            if row.saved:
                raise HTTPException(
                    status_code=409, detail="برگه شامل ردیف ثبت‌شده است و دور ریخته نمی‌شود."
                )
        db.query(RegistrationRow).filter(RegistrationRow.transmittal_id == transmittal.id).delete()
        transmittal.meta = {**(transmittal.meta or {}), "kind": "registration", "discarded": True}
        db.delete(transmittal)
        db.commit()
    return Response(status_code=204)
