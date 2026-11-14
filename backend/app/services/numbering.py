"""Atomic allocation of permanent Transmittal numbers.

New manual transmittals use the project identity format::

    {contract_no}-{contractor_code}-{parent_company_code}-{type}-{counter:04d}

For example ``7001-IP-MR-OT-0001``.  The two company codes are shown as
separate segments to keep the identity readable in the RTL workspace.  The counter row is locked in the issuing
transaction, and the project/type scope is deliberately independent for OT and
OS.  Existing ``internal``/``external`` integrations continue to use their
configured legacy template; this compatibility path does not rewrite any old
number and can be removed after those integrations are migrated to OT/OS.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import NumberSequence, Project
from app.services.project_settings import merge_settings
from app.utils.jalali import gregorian_to_jalali

MAX_RETRIES = 5
COMPANY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
NEW_TRANSMITTAL_TYPES = frozenset({"OT", "OS"})


class NumberingConfigurationError(ValueError):
    """A project is not complete enough to issue a new OT/OS number."""


def _type_value(transmittal_type: object | None) -> str | None:
    if transmittal_type is None:
        return None
    value = getattr(transmittal_type, "value", transmittal_type)
    return str(value).strip().upper()


def _project_company_code(project: Project, attribute: str, setting_key: str, label: str) -> str:
    """Read a first-class code, with a compatibility fallback for old JSON data."""
    value = getattr(project, attribute, None)
    if value is None:
        value = (project.settings or {}).get(setting_key)
    value = str(value or "").strip().upper()
    if not COMPANY_CODE_PATTERN.fullmatch(value):
        raise NumberingConfigurationError(
            f"{label} پروژه تکمیل نشده است؛ برای صدور ترانسمیتال جدید، "
            f"کد {label} را دقیقاً با دو حرف انگلیسی A-Z ثبت کنید."
        )
    return value


def missing_company_codes(project: Project) -> list[str]:
    """Return user-facing names of missing/invalid codes without raising."""
    missing: list[str] = []
    for attribute, setting_key, label in (
        ("contractor_code", "contractor_code", "پیمانکار"),
        ("parent_company_code", "parent_company_code", "شرکت مادر"),
    ):
        value = getattr(project, attribute, None)
        if value is None:
            value = (project.settings or {}).get(setting_key)
        if not COMPANY_CODE_PATTERN.fullmatch(str(value or "").strip().upper()):
            missing.append(label)
    return missing


def generate_transmittal_no(
    project: Project,
    transmittal_type: str,
    counter: int = 1,
) -> str:
    """Format a new OT/OS number for ``project`` and an allocated counter.

    Allocation and formatting are intentionally separate: callers must obtain
    ``counter`` through :func:`allocate`, while this pure function makes the
    required format easy to test and reuse in previews.
    """
    type_token = _type_value(transmittal_type)
    if type_token not in NEW_TRANSMITTAL_TYPES:
        raise ValueError("نوع ترانسمیتال جدید فقط می‌تواند OT یا OS باشد.")
    if counter < 1 or counter > 9999:
        raise NumberingConfigurationError("شمارنده ترانسمیتال باید بین 0001 و 9999 باشد.")
    contract_no = str(project.code or "").strip()
    if not contract_no:
        raise NumberingConfigurationError("شماره قرارداد پروژه برای تولید شماره ترانسمیتال خالی است.")
    contractor_code = _project_company_code(
        project, "contractor_code", "contractor_code", "پیمانکار"
    )
    parent_code = _project_company_code(
        project, "parent_company_code", "parent_company_code", "شرکت مادر"
    )
    # The sample/reference notation renders the two company codes as separate
    # segments: 7001-IP-MR-OT-0001.
    return f"{contract_no}-{contractor_code}-{parent_code}-{type_token}-{counter:04d}"


def _legacy_scope_key(scope: str, project: Project, now: datetime) -> str:
    if scope == "monthly":
        jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
        return f"transmittal:{jy}-{jm:02d}"
    if scope == "yearly":
        jy, _, _ = gregorian_to_jalali(now.year, now.month, now.day)
        return f"transmittal:{jy}"
    return f"transmittal:{project.code}"


def _scope_key(
    scope: str,
    project: Project,
    now: datetime,
    transmittal_type: str | None = None,
) -> str:
    """Return a stable counter scope; OT/OS are always independent."""
    type_token = _type_value(transmittal_type)
    if type_token in NEW_TRANSMITTAL_TYPES:
        # Include the contract value as well as project_id. If an administrator
        # changes a project's contract number, the new identity starts its own
        # sequence and no previously issued number is modified.
        return f"transmittal:{project.code}:{type_token}"
    return _legacy_scope_key(scope, project, now)


def _format(template: str, project: Project, seq: int, now: datetime) -> str:
    jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    values = {
        "project": project.code,
        "seq": seq,
        "yy": f"{jy % 100:02d}",
        "yyyy": jy,
        "mm": f"{jm:02d}",
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return f"TRN-{project.code}-{seq:04d}"


def _next_sequence(
    db: Session,
    project: Project,
    scope_key: str,
) -> tuple[NumberSequence, int]:
    """Lock or create one counter row without rolling back the outer issue.

    The savepoint is important for the first concurrent allocation: if both
    transactions try to insert the same unique row, only the savepoint is
    rolled back, then the loser locks and reads the row created by the winner.
    """
    for _ in range(MAX_RETRIES):
        sequence = db.execute(
            select(NumberSequence)
            .where(
                NumberSequence.project_id == project.id,
                NumberSequence.scope_key == scope_key,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if sequence is None:
            candidate = NumberSequence(
                project_id=project.id,
                scope_key=scope_key,
                next_value=1,
            )
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                sequence = candidate
            except IntegrityError:
                # The unique constraint means another transaction won the
                # insert. The next loop iteration obtains its locked row.
                continue
        seq = sequence.next_value
        sequence.next_value = seq + 1
        db.flush()
        return sequence, seq
    raise RuntimeError("تخصیص شماره Transmittal پس از چند تلاش ناموفق بود.")


def peek_temp(
    db: Session,
    project: Project,
    *,
    transmittal_type: str | None = None,
    settings: dict | None = None,
    now: datetime | None = None,
) -> str:
    """Return the next display-only number without consuming it."""
    cfg = merge_settings(settings if settings is not None else project.settings)
    now = now or datetime.now()
    type_token = _type_value(transmittal_type)
    use_new_format = type_token in NEW_TRANSMITTAL_TYPES and not missing_company_codes(project)
    scope_key = _scope_key(
        cfg["transmittal_sequence_scope"],
        project,
        now,
        type_token if use_new_format else None,
    )
    row = db.execute(
        select(NumberSequence).where(
            NumberSequence.project_id == project.id,
            NumberSequence.scope_key == scope_key,
        )
    ).scalar_one_or_none()
    next_seq = row.next_value if row is not None else 1
    if type_token in NEW_TRANSMITTAL_TYPES and not missing_company_codes(project):
        return generate_transmittal_no(project, type_token, next_seq)
    # A legacy project without the two new codes remains operable. Its detail
    # response carries a visible warning and the administrator can complete the
    # project before the next issue to opt into the new format.
    template = cfg.get("transmittal_temp_number_format") or "TM-{project}-Temp{seq:04d}"
    return _format(template, project, next_seq, now)


def allocate(
    db: Session,
    project: Project,
    *,
    transmittal_type: str | None = None,
    settings: dict | None = None,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Atomically return ``(number, counter)`` inside the issuing transaction.

    Passing OT or OS selects the new contract/company-code format. Omitting the
    type retains the old helper's configurable-template behaviour for callers
    that have not yet migrated; the issue service always passes the persisted
    transmittal type explicitly.
    """
    cfg = merge_settings(settings if settings is not None else project.settings)
    now = now or datetime.now()
    type_token = _type_value(transmittal_type)
    use_new_format = type_token in NEW_TRANSMITTAL_TYPES and not missing_company_codes(project)
    scope_key = _scope_key(
        cfg["transmittal_sequence_scope"],
        project,
        now,
        type_token if use_new_format else None,
    )
    _, seq = _next_sequence(db, project, scope_key)
    if use_new_format:
        return generate_transmittal_no(project, type_token, seq), seq
    return _format(cfg["transmittal_number_format"], project, seq, now), seq


def peek_next(
    db: Session,
    project: Project,
    *,
    transmittal_type: str | None = None,
    settings: dict | None = None,
    now: datetime | None = None,
) -> str:
    """Preview the next permanent number without consuming it."""
    cfg = merge_settings(settings if settings is not None else project.settings)
    now = now or datetime.now()
    type_token = _type_value(transmittal_type)
    use_new_format = type_token in NEW_TRANSMITTAL_TYPES and not missing_company_codes(project)
    scope_key = _scope_key(
        cfg["transmittal_sequence_scope"],
        project,
        now,
        type_token if use_new_format else None,
    )
    sequence = db.execute(
        select(NumberSequence).where(
            NumberSequence.project_id == project.id,
            NumberSequence.scope_key == scope_key,
        )
    ).scalar_one_or_none()
    seq = sequence.next_value if sequence else 1
    if use_new_format:
        return generate_transmittal_no(project, type_token, seq)
    return _format(cfg["transmittal_number_format"], project, seq, now)


def next_document_rev_sequence(db: Session, project_id: uuid.UUID) -> int:
    """Unused placeholder kept for symmetry; revisions are numbered per document."""
    return 0
