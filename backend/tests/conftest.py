"""Shared fixtures. The suite runs on SQLite + local storage (no services needed)."""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

# ---- environment must be set before importing the app ----------------------
_TMP = tempfile.mkdtemp(prefix="edms-test-")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_TMP}/test.db",
        "STORAGE_BACKEND": "local",
        "LOCAL_STORAGE_PATH": f"{_TMP}/storage",
        "SECRET_KEY": "test-secret-key-not-for-production",
        "AV_ENABLED": "false",
        "OCR_ENABLED": "false",
        "VLM_ENABLED": "false",
        "CELERY_TASK_ALWAYS_EAGER": "true",
        "ENVIRONMENT": "test",
        "DEBUG": "false",
    }
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import (  # noqa: E402
    DDMRule,
    DDMRuleRecipient,
    Discipline,
    MDLItem,
    Project,
    ProjectMember,
    Unit,
    User,
)
from app.models.enums import ActionRequired, ProjectRole, PurposeOfIssue  # noqa: E402
from app.services.normalization import normalize_code, normalize_title  # noqa: E402
from app.services.project_settings import merge_settings  # noqa: E402

engine = create_engine(f"sqlite:///{_TMP}/test.db", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _isolate_database():
    """Truncate every table after each test so tests are order-independent."""
    yield
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys = OFF"))
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
        connection.execute(text("PRAGMA foreign_keys = ON"))


@pytest.fixture()
def db() -> Session:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def project(db: Session) -> Project:
    project = Project(
        code="TST",
        name="پروژه آزمون",
        client_name="کارفرما",
        contractor_name="پیمانکار",
        settings=merge_settings(None),
    )
    db.add(project)
    db.flush()
    civ = Discipline(project_id=project.id, code="CIV", name="عمران")
    mec = Discipline(project_id=project.id, code="MEC", name="مکانیک")
    unit = Unit(project_id=project.id, code="U-100", name="یوتیلیتی")
    db.add_all([civ, mec, unit])
    db.flush()
    for code, title, discipline, rev in (
        ("TST-CIV-DR-001", "Plot Plan", civ, "A"),
        ("TST-CIV-DR-002", "Site Grading", civ, "B"),
        ("TST-MEC-DR-001", "Piping Layout", mec, "A"),
    ):
        db.add(
            MDLItem(
                project_id=project.id,
                doc_code=code,
                doc_code_norm=normalize_code(code),
                title=title,
                title_norm=normalize_title(title),
                discipline_id=discipline.id,
                unit_id=unit.id,
                doc_type="DWG",
                current_rev=rev,
                status="Draft",
            )
        )
    db.commit()
    db.refresh(project)
    return project


@pytest.fixture()
def users(db: Session, project: Project) -> dict[str, User]:
    created: dict[str, User] = {}
    for key, email, role in (
        ("admin", "admin@edms-test.com", ProjectRole.SYSTEM_ADMIN),
        ("dcc", "dcc@edms-test.com", ProjectRole.DCC),
        ("reviewer", "reviewer@edms-test.com", ProjectRole.REVIEWER),
        ("submitter", "submitter@edms-test.com", ProjectRole.SUBMITTER),
        ("pm", "pm@edms-test.com", ProjectRole.PROJECT_MANAGER),
    ):
        user = User(
            email=email,
            full_name=key,
            hashed_password=hash_password("secret123"),
            is_superuser=(key == "admin"),
        )
        db.add(user)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        created[key] = user
    db.commit()
    return created


@pytest.fixture()
def ddm_rules(db: Session, project: Project, users: dict[str, User]) -> DDMRule:
    discipline = db.query(Discipline).filter_by(project_id=project.id, code="CIV").one()
    rule = DDMRule(
        project_id=project.id,
        name="CIV drawings",
        discipline_id=discipline.id,
        doc_type="DWG",
        purpose=PurposeOfIssue.IFR,
        due_days=10,
        priority=10,
    )
    db.add(rule)
    db.flush()
    db.add(
        DDMRuleRecipient(rule_id=rule.id, user_id=users["reviewer"].id, action=ActionRequired.REVIEW)
    )
    db.add(DDMRuleRecipient(rule_id=rule.id, user_id=users["pm"].id, action=ActionRequired.INFO))
    db.commit()
    db.refresh(rule)
    return rule


@pytest.fixture()
def client(db: Session) -> TestClient:
    def override_get_db():
        try:
            yield db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


def auth_headers(client: TestClient, email: str, password: str = "secret123") -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
