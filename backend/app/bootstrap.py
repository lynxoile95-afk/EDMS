"""Idempotent bootstrap: admin user + (optional) demo project.

Run inside the API container:  python -m app.bootstrap
Set SEED_DEMO=true to create a demo project with disciplines, a DDM rule and a
small MDL so the UI has something to show on first launch.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import session_scope
from app.models import (
    DDMRule,
    DDMRuleRecipient,
    Discipline,
    MDLItem,
    Project,
    ProjectMember,
    TitleBlockTemplate,
    Unit,
    User,
)
from app.models.enums import ActionRequired, ProjectRole
from app.services.normalization import normalize_code, normalize_title
from app.services.project_settings import merge_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bootstrap")

#: The eight accounts one project needs for the review route:
#: contractor → document control → management → four discipline engineers.
#: The fourth element is the discipline code for a discipline engineer, else None.
DEMO_USERS: list[tuple[str, str, ProjectRole, str | None]] = [
    ("submitter@example.com", "مهندس رضا کریمی — پیمانکار", ProjectRole.SUBMITTER, None),
    ("dcc@example.com", "مهندس مریم رستمی — کارشناس کنترل اسناد مهندسی", ProjectRole.DCC, None),
    ("pm@example.com", "مهندس بهرام توکلی — مدیر پروژه", ProjectRole.PROJECT_MANAGER, None),
    ("em@example.com", "مهندس فرهاد موحد — مدیر مهندسی", ProjectRole.ENGINEERING_MANAGER, None),
    ("ele@example.com", "مهندس امیر رضایی — کارشناس برق", ProjectRole.REVIEWER, "ELE"),
    ("civ@example.com", "مهندس سارا محمدی — کارشناس سیویل", ProjectRole.REVIEWER, "CIV"),
    ("arc@example.com", "مهندس نگار حسینی — کارشناس معماری", ProjectRole.REVIEWER, "ARC"),
    ("mec@example.com", "مهندس حامد صادقی — کارشناس تاسیسات", ProjectRole.REVIEWER, "MEC"),
]

DEMO_DOCS = [
    ("PRJ-CIV-DR-0101", "Plot Plan / پلان موقعیت", "CIV", "U-100", "DWG", "A"),
    ("PRJ-CIV-DR-0102", "Site Grading Plan / پلان شیب‌بندی", "CIV", "U-100", "DWG", "B"),
    ("PRJ-MEC-DR-0201", "Piping Layout / چیدمان لوله‌کشی", "MEC", "U-200", "DWG", "A"),
    ("PRJ-MEC-DS-0202", "Pump Datasheet / دیتاشیت پمپ", "MEC", "U-200", "DAT", "0"),
    ("PRJ-ELE-DR-0301", "Single Line Diagram / دیاگرام تک‌خطی", "ELE", "U-300", "DWG", "A"),
]


def ensure_admin() -> User:
    with session_scope() as db:
        user = db.execute(
            select(User).where(User.email == settings.bootstrap_admin_email.lower())
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=settings.bootstrap_admin_email.lower(),
                full_name="مدیر سیستم",
                hashed_password=hash_password(settings.bootstrap_admin_password),
                is_superuser=True,
                auth_provider="local",
            )
            db.add(user)
            db.flush()
            logger.info("created admin user %s", user.email)
        else:
            logger.info("admin user already exists")
        db.expunge(user)
        return user


def seed_demo() -> None:
    with session_scope() as db:
        project = db.execute(select(Project).where(Project.code == "DEMO")).scalar_one_or_none()
        if project is not None:
            logger.info("demo project already exists")
            return

        project = Project(
            code="DEMO",
            name="پروژه نمایشی — پالایشگاه نمونه",
            client_name="کارفرمای نمونه",
            contractor_name="پیمانکار نمونه",
            settings=merge_settings(None),
        )
        db.add(project)
        db.flush()

        admin = db.execute(
            select(User).where(User.email == settings.bootstrap_admin_email.lower())
        ).scalar_one()
        db.add(ProjectMember(project_id=project.id, user_id=admin.id, role=ProjectRole.SYSTEM_ADMIN))

        disciplines: dict[str, Discipline] = {}
        for code, name in (
            ("CIV", "سیویل / عمران"),
            ("MEC", "تاسیسات / مکانیک"),
            ("ELE", "برق"),
            ("ARC", "معماری"),
            ("PRO", "فرآیند"),
        ):
            discipline = Discipline(project_id=project.id, code=code, name=name)
            db.add(discipline)
            disciplines[code] = discipline
        units: dict[str, Unit] = {}
        for code, name in (("U-100", "یوتیلیتی"), ("U-200", "واحد فرآیند"), ("U-300", "واحد برق")):
            unit = Unit(project_id=project.id, code=code, name=name)
            db.add(unit)
            units[code] = unit
        db.flush()

        for email, full_name, role, discipline_code in DEMO_USERS:
            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if user is None:
                user = User(
                    email=email,
                    full_name=full_name,
                    hashed_password=hash_password("Demo1234!"),
                    auth_provider="local",
                )
                db.add(user)
                db.flush()
            else:
                # keep the display name in step with the seed (idempotent re-runs)
                user.full_name = full_name
            db.add(
                ProjectMember(
                    project_id=project.id,
                    user_id=user.id,
                    role=role,
                    discipline_id=disciplines[discipline_code].id if discipline_code else None,
                )
            )

        for doc_code, title, discipline, unit, doc_type, rev in DEMO_DOCS:
            db.add(
                MDLItem(
                    project_id=project.id,
                    doc_code=doc_code,
                    doc_code_norm=normalize_code(doc_code),
                    title=title,
                    title_norm=normalize_title(title),
                    discipline_id=disciplines[discipline].id,
                    unit_id=units[unit].id,
                    doc_type=doc_type,
                    current_rev=rev,
                    status="Draft",
                    due_date=date.today() + timedelta(days=30),
                    weight=1.0,
                    page_count=1,
                )
            )
        db.flush()

        # Each discipline rule names that discipline's own engineer: the review of a
        # Civil drawing must land on the Civil engineer, not on a generic reviewer.
        engineer_by_discipline = {
            "MEC": "mec@example.com",
            "CIV": "civ@example.com",
            "ELE": "ele@example.com",
            "ARC": "arc@example.com",
        }
        dcc = db.execute(select(User).where(User.email == "dcc@example.com")).scalar_one()
        pm = db.execute(select(User).where(User.email == "pm@example.com")).scalar_one()
        em = db.execute(select(User).where(User.email == "em@example.com")).scalar_one()
        for discipline_code, doc_type, due in (
            ("MEC", "DWG", 10),
            ("CIV", "DWG", 14),
            ("ELE", "DWG", 14),
            ("ARC", "DWG", 14),
        ):
            engineer = db.execute(
                select(User).where(User.email == engineer_by_discipline[discipline_code])
            ).scalar_one()
            rule = DDMRule(
                project_id=project.id,
                name=f"توزیع {discipline_code} — نقشه",
                discipline_id=disciplines[discipline_code].id,
                doc_type=doc_type,
                # Purpose left open on purpose: discipline routing applies to every
                # P.O.I (IFR/IFC/IFE/…). Pinning IFR made a later IFC issue fall back
                # to the catch-all rule and silently lose the discipline reviewers.
                purpose=None,
                default_action=ActionRequired.REVIEW,
                due_days=due,
                priority=10,
            )
            db.add(rule)
            db.flush()
            db.add(
                DDMRuleRecipient(rule_id=rule.id, user_id=engineer.id, action=ActionRequired.REVIEW)
            )
            db.add(DDMRuleRecipient(rule_id=rule.id, user_id=dcc.id, action=ActionRequired.INFO))
            db.add(DDMRuleRecipient(rule_id=rule.id, user_id=em.id, action=ActionRequired.INFO))
            db.add(DDMRuleRecipient(rule_id=rule.id, user_id=pm.id, action=ActionRequired.INFO))

        fallback = DDMRule(
            project_id=project.id,
            name="قاعده پیش‌فرض (همه مدارک)",
            purpose=None,
            default_action=ActionRequired.INFO,
            due_days=14,
            priority=999,
        )
        db.add(fallback)
        db.flush()
        db.add(DDMRuleRecipient(rule_id=fallback.id, user_id=dcc.id, action=ActionRequired.INFO))

        db.add(
            TitleBlockTemplate(
                project_id=project.id,
                name="قالب پیش‌فرض A1 — کادر پایین راست",
                page_aspect_ratios=[[1.30, 1.50]],
                regions={"title_block": {"x": 0.55, "y": 0.62, "w": 0.45, "h": 0.38}},
                anchor_keywords=["document", "title", "rev", "مدرک"],
                field_regex={},
                priority=50,
            )
        )
        logger.info("demo project seeded: %s documents", len(DEMO_DOCS))


def main() -> None:
    ensure_admin()
    if os.getenv("SEED_DEMO", "false").lower() in {"1", "true", "yes"}:
        seed_demo()
    logger.info("bootstrap finished")


if __name__ == "__main__":
    main()
