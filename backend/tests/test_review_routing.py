"""Referral chain: contractor → document control → engineering manager → discipline.

This is the organisation's real review route, so the test walks it with the eight
accounts the project actually holds: one contractor, one document controller, one
project manager, one engineering manager and four discipline engineers
(Electrical, Civil, Architecture, Utilities).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.models import MDLItem, ProjectMember, User
from app.models.enums import ProjectRole
from tests.conftest import auth_headers
from tests.test_api_flow import _seed_result


def _register_revision(client: TestClient, db, project, users) -> str:
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    registered = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=dcc,
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "B"},
    )
    assert registered.status_code == 200, registered.text
    return registered.json()["revision_id"]


def _discipline_user(db, project, code: str) -> User:
    """Create (or return) a discipline engineer of this project."""
    email = f"{code.lower()}-engineer@edms-test.com"
    user = db.query(User).filter_by(email=email).one_or_none()
    if user is None:
        assigned = (
            db.query(ProjectMember)
            .filter(ProjectMember.project_id == project.id, ProjectMember.role == ProjectRole.REVIEWER)
            .first()
        )
        user = User(
            email=email,
            full_name=f"کارشناس {code}",
            hashed_password=assigned.user.hashed_password if assigned else None,
            is_active=True,
        )
        db.add(user)
        db.flush()
        discipline = next(d for d in project.disciplines if d.code == code)
        db.add(
            ProjectMember(
                project_id=project.id, user_id=user.id, role=ProjectRole.REVIEWER, discipline_id=discipline.id
            )
        )
        db.commit()
    return user


def _manager(db, project, role: ProjectRole) -> User:
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project.id, ProjectMember.role == role)
        .first()
    )
    assert member is not None, f"no {role} seeded"
    return member.user


def _issue(client: TestClient, db, project, users, ddm_rules) -> str:
    from app.services import transmittal_service
    from app.models import DocumentRevision
    from app.models.enums import PurposeOfIssue, TransmittalType

    revision_id = _register_revision(client, db, project, users)
    revision = db.get(DocumentRevision, uuid.UUID(revision_id))
    draft = transmittal_service.create_draft_transmittals(
        db,
        project=project,
        revisions=[revision],
        purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL,
        actor=users["dcc"],
    )[0]
    db.commit()
    issued = client.post(
        f"/api/v1/transmittals/{draft.id}/issue",
        headers=auth_headers(client, "dcc@edms-test.com"),
        json={"notify": False},
    )
    assert issued.status_code == 200, issued.text
    return str(draft.id)


def test_full_referral_chain(client: TestClient, db, project, users, ddm_rules):
    transmittal_id = _issue(client, db, project, users, ddm_rules)
    dcc = auth_headers(client, "dcc@edms-test.com")
    engineering_manager = _manager(db, project, ProjectRole.ENGINEERING_MANAGER)
    em = auth_headers(client, engineering_manager.email)

    # 1. issuing puts the sheet in document control's hands
    detail = client.get(f"/api/v1/transmittals/{transmittal_id}", headers=dcc).json()
    assert detail["stage"] == "dcc"

    # 2. document control forwards it to the engineering manager
    forwarded = client.post(
        f"/api/v1/transmittals/{transmittal_id}/forward",
        headers=dcc,
        json={"to_user_id": str(engineering_manager.id), "note": "کنترل شد؛ جهت ارجاع به دیسیپلین"},
    )
    assert forwarded.status_code == 200, forwarded.text
    assert forwarded.json()["stage"] == "management"

    # 3. the engineering manager refers it to the Civil engineer
    civil = _discipline_user(db, project, "CIV")
    referred = client.post(
        f"/api/v1/transmittals/{transmittal_id}/refer",
        headers=em,
        json={"discipline_id": str(next(d.id for d in project.disciplines if d.code == "CIV")), "note": "بررسی نقشه پلان"},
    )
    assert referred.status_code == 200, referred.text
    body = referred.json()
    assert body["stage"] == "discipline"
    assert body["referrals"][-1]["to_user_id"] == str(civil.id)
    assert body["referrals"][-1]["status"] == "pending"

    # 4. only the referred engineer sees it in "my inbox"
    mine = client.get(
        f"/api/v1/projects/{project.id}/transmittals?mine=true", headers=auth_headers(client, civil.email)
    ).json()["items"]
    assert [str(item["id"]) for item in mine] == [transmittal_id]

    other = _discipline_user(db, project, "ELE")
    theirs = client.get(
        f"/api/v1/projects/{project.id}/transmittals?mine=true", headers=auth_headers(client, other.email)
    ).json()["items"]
    assert theirs == [], "an unrelated discipline engineer must not inherit the referral"

    # 5. the engineer completes the review and the sheet returns to management
    completed = client.post(
        f"/api/v1/transmittals/{transmittal_id}/referrals/{body['referrals'][-1]['id']}/complete",
        headers=auth_headers(client, civil.email),
        json={"note": "نقشه بررسی شد؛ دو نظر ثبت شد"},
    )
    assert completed.status_code == 200, completed.text
    final = completed.json()
    assert final["stage"] == "management"
    assert final["referrals"][-1]["status"] == "done"


def test_only_document_control_forwards_and_only_management_refers(client: TestClient, db, project, users, ddm_rules):
    transmittal_id = _issue(client, db, project, users, ddm_rules)
    contractor = auth_headers(client, "submitter@edms-test.com")

    denied = client.post(
        f"/api/v1/transmittals/{transmittal_id}/forward", headers=contractor, json={}
    )
    assert denied.status_code == 403

    dcc = auth_headers(client, "dcc@edms-test.com")
    engineering_manager = _manager(db, project, ProjectRole.ENGINEERING_MANAGER)
    client.post(
        f"/api/v1/transmittals/{transmittal_id}/forward",
        headers=dcc,
        json={"to_user_id": str(engineering_manager.id)},
    )

    # document control may not hand work straight to a discipline
    denied = client.post(
        f"/api/v1/transmittals/{transmittal_id}/refer", headers=dcc, json={}
    )
    assert denied.status_code == 403


def test_refer_requires_a_discipline_or_a_user(client: TestClient, db, project, users, ddm_rules):
    transmittal_id = _issue(client, db, project, users, ddm_rules)
    dcc = auth_headers(client, "dcc@edms-test.com")
    engineering_manager = _manager(db, project, ProjectRole.ENGINEERING_MANAGER)
    client.post(
        f"/api/v1/transmittals/{transmittal_id}/forward",
        headers=dcc,
        json={"to_user_id": str(engineering_manager.id)},
    )
    response = client.post(
        f"/api/v1/transmittals/{transmittal_id}/refer",
        headers=auth_headers(client, engineering_manager.email),
        json={},
    )
    assert response.status_code == 422
    assert "دیسیپلین" in response.json()["detail"]


def test_discipline_without_an_engineer_is_reported(client: TestClient, db, project, users, ddm_rules):
    from app.models import Discipline

    transmittal_id = _issue(client, db, project, users, ddm_rules)
    dcc = auth_headers(client, "dcc@edms-test.com")
    engineering_manager = _manager(db, project, ProjectRole.ENGINEERING_MANAGER)
    client.post(
        f"/api/v1/transmittals/{transmittal_id}/forward",
        headers=dcc,
        json={"to_user_id": str(engineering_manager.id)},
    )
    empty = Discipline(project_id=project.id, code="INS", name="ابزار دقیق")
    db.add(empty)
    db.commit()

    response = client.post(
        f"/api/v1/transmittals/{transmittal_id}/refer",
        headers=auth_headers(client, engineering_manager.email),
        json={"discipline_id": str(empty.id)},
    )
    assert response.status_code == 422
    assert "کارشناس" in response.json()["detail"]


def test_referral_creates_a_notification_for_the_engineer(client: TestClient, db, project, users, ddm_rules):
    transmittal_id = _issue(client, db, project, users, ddm_rules)
    dcc = auth_headers(client, "dcc@edms-test.com")
    engineering_manager = _manager(db, project, ProjectRole.ENGINEERING_MANAGER)
    client.post(
        f"/api/v1/transmittals/{transmittal_id}/forward",
        headers=dcc,
        json={"to_user_id": str(engineering_manager.id)},
    )
    civil = _discipline_user(db, project, "CIV")
    client.post(
        f"/api/v1/transmittals/{transmittal_id}/refer",
        headers=auth_headers(client, engineering_manager.email),
        json={"discipline_id": str(next(d.id for d in project.disciplines if d.code == "CIV"))},
    )
    inbox = client.get("/api/v1/notifications?limit=20", headers=auth_headers(client, civil.email)).json()
    assert any(item["entity_id"] == transmittal_id for item in inbox["items"])
