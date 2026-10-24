"""End-to-end API flow on SQLite: auth -> upload -> review -> transmittal -> issue."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    DocumentRevision,
    ExtractionResult,
    MDLItem,
    Sheet,
    SourceFile,
    UploadSession,
)
from app.models.enums import Decision
from app.services import rendering
from tests.conftest import auth_headers

FAKE_PDF = b"%PDF-1.4\n% test fixture\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _seed_result(db: Session, project, users) -> ExtractionResult:
    """Insert the artefacts the worker would have produced."""
    session = UploadSession(
        project_id=project.id, created_by_id=users["submitter"].id, purpose="IFR",
        mode="revision", transmittal_type="internal", status="completed", stats={},
    )
    db.add(session)
    db.flush()
    source_file = SourceFile(
        session_id=session.id,
        project_id=project.id,
        original_filename="TST-CIV-DR-001-RevB.pdf",
        storage_key="TST/incoming/test.pdf",
        size_bytes=len(FAKE_PDF),
        sha256=uuid.uuid4().hex * 2,
        page_count=1,
        status="processed",
        uploaded_by_id=users["submitter"].id,
    )
    db.add(source_file)
    db.flush()
    sheet = Sheet(
        source_file_id=source_file.id, project_id=project.id, page_no=1,
        width_pt=2384, height_pt=1684, has_text_layer=True, status="extracted",
    )
    db.add(sheet)
    db.flush()
    item = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    result = ExtractionResult(
        project_id=project.id,
        sheet_id=sheet.id,
        source_file_id=source_file.id,
        session_id=session.id,
        doc_code="TST-CIV-DR-001",
        doc_code_norm="TST-CIV-DR-001",
        rev="B",
        title="Plot Plan",
        discipline_code="CIV",
        overall_confidence=0.94,
        field_confidence={"doc_code": 0.98, "rev": 0.95, "title": 0.9},
        decision=Decision.GREEN,
        matched_mdl_item_id=item.id,
        match_score=1.0,
        match_method="exact",
        payload={},
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def test_login_rejects_bad_password(client: TestClient, users):
    response = client.post("/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "wrong"})
    assert response.status_code == 401
    assert "نادرست" in response.json()["detail"]


def test_login_and_me(client: TestClient, users):
    headers = auth_headers(client, "dcc@edms-test.com")
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["email"] == "dcc@edms-test.com"


def test_unauthenticated_request_is_401(client: TestClient, project):
    assert client.get(f"/api/v1/projects/{project.id}").status_code == 401


def test_non_member_cannot_see_project(client: TestClient, db, project, users):
    from app.core.security import hash_password
    from app.models import User

    db.add(User(email="outsider@edms-test.com", full_name="x", hashed_password=hash_password("secret123")))
    db.commit()
    headers = auth_headers(client, "outsider@edms-test.com")
    assert client.get(f"/api/v1/projects/{project.id}", headers=headers).status_code == 404


def test_project_list_and_dashboard(client: TestClient, project, users):
    headers = auth_headers(client, "dcc@edms-test.com")
    listing = client.get("/api/v1/projects", headers=headers)
    assert listing.status_code == 200
    assert any(item["code"] == "TST" for item in listing.json())

    dashboard = client.get(f"/api/v1/projects/{project.id}/dashboard", headers=headers)
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["documents_total"] == 3
    assert "auto_accept_rate" in body


def test_review_queue_and_detail(client: TestClient, db, project, users):
    result = _seed_result(db, project, users)
    headers = auth_headers(client, "dcc@edms-test.com")
    queue = client.get(f"/api/v1/projects/{project.id}/review-queue", headers=headers)
    assert queue.status_code == 200
    assert queue.json()["total"] == 1

    detail = client.get(f"/api/v1/extraction-results/{result.id}", headers=headers)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["doc_code"] == "TST-CIV-DR-001"
    assert payload["candidates"][0]["method"] == "exact"


def test_review_register_creates_revision_and_draft(client: TestClient, db, project, users, ddm_rules):
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    headers = auth_headers(client, "dcc@edms-test.com")

    response = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=headers,
        json={
            "action": "register",
            "mdl_item_id": str(item.id),
            "rev": "B",
            "create_transmittal": True,
            "purpose": "IFR",
            "notes": "تأیید شد",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision_id"]
    assert len(body["transmittal_ids"]) == 1

    revision = db.get(DocumentRevision, uuid.UUID(body["revision_id"]))
    assert revision is not None and revision.rev == "B"
    assert revision.is_current is True
    db.refresh(item)
    assert item.current_rev == "B"


def test_registering_older_revision_is_conflict(client: TestClient, db, project, users, ddm_rules):
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-002").one()  # current rev B
    headers = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=headers,
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "A"},
    )
    assert response.status_code == 409
    assert "قدیمی" in response.json()["detail"]


def test_reviewer_cannot_register(client: TestClient, db, project, users):
    result = _seed_result(db, project, users)
    headers = auth_headers(client, "reviewer@edms-test.com")
    response = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=headers,
        json={"action": "register"},
    )
    assert response.status_code == 403


def test_transmittal_issue_flow(client: TestClient, db, project, users, ddm_rules, monkeypatch):
    monkeypatch.setattr(rendering, "render_pdf", lambda context: b"%PDF-1.4 fake cover sheet")
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    dcc_headers = auth_headers(client, "dcc@edms-test.com")

    registered = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        headers=dcc_headers,
        json={
            "action": "register", "mdl_item_id": str(item.id), "rev": "B",
            "create_transmittal": True, "purpose": "IFR",
        },
    )
    assert registered.status_code == 200, registered.text
    transmittal_id = registered.json()["transmittal_ids"][0]

    detail = client.get(f"/api/v1/transmittals/{transmittal_id}", headers=dcc_headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "draft"
    assert detail.json()["preview_number"]

    issued = client.post(
        f"/api/v1/transmittals/{transmittal_id}/issue", headers=dcc_headers, json={"notify": False}
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["status"] == "issued"
    assert body["number"].startswith("TRN-TST-")
    assert body["line_count"] == 1
    assert body["recipient_count"] == 2


def test_transmittal_issue_requires_recipients(client: TestClient, db, project, users):
    """No DDM rule -> draft exists but issuing must fail with a clear message."""
    from app.services import transmittal_service
    from app.models.enums import PurposeOfIssue, TransmittalType

    item = db.query(MDLItem).filter_by(doc_code_norm="TST-MEC-DR-001").one()
    revision = DocumentRevision(
        project_id=project.id, mdl_item_id=item.id, rev="B", rev_seq=2,
        file_hash=uuid.uuid4().hex * 2, page_count=1, is_current=True,
    )
    db.add(revision)
    db.commit()
    draft = transmittal_service.create_draft_transmittals(
        db, project=project, revisions=[revision], purpose=PurposeOfIssue.IFR,
        transmittal_type=TransmittalType.INTERNAL, actor=users["dcc"],
    )[0]
    db.commit()
    headers = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/transmittals/{draft.id}/issue", headers=headers, json={"notify": False}
    )
    assert response.status_code == 422
    assert "گیرنده" in response.json()["detail"]


def test_comment_list_flow(client: TestClient, db, project, users, ddm_rules):
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    reviewer = auth_headers(client, "reviewer@edms-test.com")
    submitter = auth_headers(client, "submitter@edms-test.com")

    registered = client.post(
        f"/api/v1/extraction-results/{result.id}/review", headers=dcc,
        json={"action": "register", "mdl_item_id": str(item.id), "rev": "B"},
    ).json()
    revision_id = registered["revision_id"]

    created = client.post(
        f"/api/v1/revisions/{revision_id}/comments",
        headers=reviewer,
        json={"revision_id": revision_id, "text": "ابعاد پلان اصلاح شود", "code": "2", "page_no": 1},
    )
    assert created.status_code == 201, created.text
    comment_id = created.json()["id"]

    responded = client.post(
        f"/api/v1/comments/{comment_id}/respond", headers=submitter,
        json={"response_text": "اصلاح شد در Rev C"},
    )
    assert responded.status_code == 200
    assert responded.json()["status"] == "answered"

    closed = client.post(f"/api/v1/comments/{comment_id}/close", headers=reviewer)
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_upload_session_endpoint(client: TestClient, project, users):
    headers = auth_headers(client, "submitter@edms-test.com")
    created = client.post(
        f"/api/v1/projects/{project.id}/upload-sessions",
        headers=headers,
        json={"purpose": "IFR", "mode": "revision", "transmittal_type": "internal"},
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    files = {"files": ("TST-CIV-DR-001.pdf", FAKE_PDF, "application/pdf")}
    uploaded = client.post(f"/api/v1/upload-sessions/{session_id}/files", headers=headers, files=files)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()[0]["sha256"]

    fetched = client.get(f"/api/v1/upload-sessions/{session_id}", headers=headers)
    assert fetched.status_code == 200
    assert len(fetched.json()["files"]) == 1


def test_upload_rejects_non_pdf(client: TestClient, project, users):
    headers = auth_headers(client, "submitter@edms-test.com")
    session_id = client.post(
        f"/api/v1/projects/{project.id}/upload-sessions", headers=headers, json={"purpose": "IFR"}
    ).json()["id"]
    files = {"files": ("evil.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")}
    response = client.post(f"/api/v1/upload-sessions/{session_id}/files", headers=headers, files=files)
    assert response.status_code == 422


def test_ddm_simulate(client: TestClient, project, users, ddm_rules):
    headers = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/ddm/simulate",
        headers=headers,
        json={"discipline_code": "CIV", "doc_type": "DWG", "purpose": "IFR"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["rule_name"] == "CIV drawings"


def test_validation_error_is_persian(client: TestClient):
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422
    assert "معتبر نیستند" in response.json()["detail"]


def test_refresh_rotates_tokens(client: TestClient, users):
    login = client.post(
        "/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"}
    )
    pair = login.json()
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    new_pair = refreshed.json()
    assert new_pair["access_token"] != pair["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_pair['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "dcc@edms-test.com"


def test_refresh_rejects_garbage_and_wrong_token_type(client: TestClient, users):
    bad = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert bad.status_code == 401

    pair = client.post(
        "/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"}
    ).json()
    # an access token must not be usable as a refresh token
    wrong = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["access_token"]})
    assert wrong.status_code == 401


def test_token_with_non_uuid_subject_is_401_not_500(client: TestClient, users):
    """Regression: the refresh route used to crash with a 500 on a string subject."""
    from app.core.security import create_access_token, create_refresh_token

    for token in (create_refresh_token("not-a-uuid"), create_access_token("not-a-uuid")):
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401
        assert "detail" in response.json()

    bad_access = create_access_token("not-a-uuid")
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_access}"})
    assert response.status_code == 401


def test_disabled_user_cannot_refresh(client: TestClient, db, project, users):
    pair = client.post(
        "/api/v1/auth/login", json={"email": "submitter@edms-test.com", "password": "secret123"}
    ).json()
    user = db.query(type(users["submitter"])).filter_by(email="submitter@edms-test.com").one()
    user.is_active = False
    db.commit()

    response = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert response.status_code == 401
    assert "یافت نشد" in response.json()["detail"]


def test_me_accepts_token_from_cookie(client: TestClient, users):
    """Auth must survive a proxy that strips the Authorization header."""
    pair = client.post(
        "/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"}
    ).json()
    client.cookies.clear()
    client.cookies.set("edms_access_token", pair["access_token"])
    response = client.get("/api/v1/auth/me", headers={"Authorization": ""})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "dcc@edms-test.com"


def test_me_accepts_token_from_custom_header(client: TestClient, users):
    pair = client.post(
        "/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"}
    ).json()
    client.cookies.clear()
    response = client.get("/api/v1/auth/me", headers={"X-Access-Token": pair["access_token"]})
    assert response.status_code == 200, response.text


def test_login_sets_session_cookies(client: TestClient, users):
    response = client.post(
        "/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"}
    )
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert any(cookie.startswith("edms_access_token=") for cookie in cookies)
    assert any(cookie.startswith("edms_refresh_token=") for cookie in cookies)
    assert all("HttpOnly" in cookie for cookie in cookies)


def test_refresh_works_from_cookie_only(client: TestClient, users):
    pair = client.post(
        "/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"}
    ).json()
    client.cookies.clear()
    client.cookies.set("edms_refresh_token", pair["refresh_token"])
    response = client.post("/api/v1/auth/refresh", json={})
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


def test_logout_clears_cookies(client: TestClient, users):
    client.post("/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"})
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    cleared = response.headers.get_list("set-cookie")
    assert any("edms_access_token=" in cookie and "Max-Age=0" in cookie for cookie in cleared)


def test_unauthorized_response_has_www_authenticate(client: TestClient):
    response = client.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_transport_check_reports_where_the_token_came_from(client: TestClient, users):
    pair = client.post(
        "/api/v1/auth/login", json={"email": "dcc@edms-test.com", "password": "secret123"}
    ).json()

    client.cookies.clear()  # the login response planted session cookies
    anonymous = client.get("/api/v1/auth/transport-check")
    assert anonymous.status_code == 200
    assert anonymous.json()["token_sources"] == []
    assert anonymous.json()["token_valid"] is False

    bearer = client.get(
        "/api/v1/auth/transport-check", headers={"Authorization": f"Bearer {pair['access_token']}"}
    ).json()
    assert bearer["token_sources"] == ["authorization"]
    assert bearer["token_valid"] is True
    assert bearer["authorization_header_present"] is True

    client.cookies.clear()
    client.cookies.set("edms_access_token", pair["access_token"])
    cookie = client.get("/api/v1/auth/transport-check").json()
    assert cookie["token_sources"] == ["cookie"]
    assert cookie["token_valid"] is True


def test_purpose_of_issue_supports_reference_vocabulary(client: TestClient, project, users):
    """The organisation's own EDMS uses IPC / IFE / IFP / IFC (see docs/03)."""
    headers = auth_headers(client, "dcc@edms-test.com")
    for purpose in ("IFC", "IFE", "IFP", "IPC"):
        response = client.post(
            f"/api/v1/projects/{project.id}/upload-sessions",
            json={"purpose": purpose, "mode": "revision", "transmittal_type": "internal"},
            headers=headers,
        )
        assert response.status_code == 201, f"{purpose}: {response.text}"
        assert response.json()["purpose"] == purpose

    bad = client.post(
        f"/api/v1/projects/{project.id}/upload-sessions",
        json={"purpose": "XYZ", "mode": "revision"},
        headers=headers,
    )
    assert bad.status_code == 422


def test_draft_transmittal_shows_temporary_number(client: TestClient, db, project, users, ddm_rules):
    result = _seed_result(db, project, users)
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    headers = auth_headers(client, "dcc@edms-test.com")
    review = client.post(
        f"/api/v1/extraction-results/{result.id}/review",
        json={
            "action": "register",
            "mdl_item_id": str(item.id),
            "rev": "C",
            "title": "Plot Plan",
            "create_transmittal": True,
            "purpose": "IFR",
            "override_issues": True,
        },
        headers=headers,
    )
    transmittal_id = review.json()["transmittal_ids"][0]
    detail = client.get(f"/api/v1/transmittals/{transmittal_id}", headers=headers).json()
    assert detail["number"] is None
    assert detail["preview_number"].startswith("TM-")
    assert "Temp" in detail["preview_number"]
