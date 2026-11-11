from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def _manual_afc(client: TestClient, project, users, db):
    from app.models import MDLItem

    item = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    created = client.post(
        f"/api/v1/projects/{project.id}/transmittals/manual",
        headers=auth_headers(client, "dcc@edms-test.com"),
        json={
            "rows": [{"mdl_item_id": str(item.id), "revision": "A", "doc_status": "No Comment"}],
            "purpose": "AFC",
            "type": "OT",
            "final_approver_id": str(users["pm"].id),
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_no_comment_creates_completed_reply_sheet_and_updates_line(client, project, users, db):
    body = _manual_afc(client, project, users, db)
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/no-comment",
        headers=dcc,
        json={},
    )
    assert response.status_code == 201, response.text
    sheet = response.json()
    assert sheet["status"] == "completed"
    assert sheet["judgment"] == "OK"
    assert sheet["doc_status"] == "No Comment"
    assert sheet["doc_percent"] == 100
    assert sheet["rows"][0]["status"] == "No Comment"
    assert sheet["rows"][0]["doc_percent"] == 100
    assert sheet["number"]
    assert sheet["replied_by_name"] == "dcc"
    assert sheet["flow_history"]


def test_reply_sheet_pdf_and_no_comment_endpoint_without_body(client, project, users, db):
    body = _manual_afc(client, project, users, db)
    response = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/no-comment",
        headers=auth_headers(client, "dcc@edms-test.com"),
    )
    assert response.status_code == 201, response.text
    pdf = client.get(
        f"/api/v1/transmittals/{body['id']}/reply-sheet.pdf",
        headers=auth_headers(client, "dcc@edms-test.com"),
    )
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF")


def test_afc_finalize_is_project_manager_only_and_voids_previous(client, project, users, db):
    from app.models import DocumentRevision, MDLItem

    body = _manual_afc(client, project, users, db)
    dcc = auth_headers(client, "dcc@edms-test.com")
    created_sheet = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/no-comment", headers=dcc, json={}
    )
    assert created_sheet.status_code == 201, created_sheet.text
    denied = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/no-comment-finalize",
        headers=dcc,
        json={},
    )
    assert denied.status_code == 403, denied.text
    finalized = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/no-comment-finalize",
        headers=auth_headers(client, "pm@edms-test.com"),
        json={},
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "final"
    assert finalized.json()["is_final"] is True
    assert finalized.json()["lines"][0]["doc_status"] == "No Comment"
    item = db.query(MDLItem).filter_by(doc_code_norm="TST-CIV-DR-001").one()
    assert item.status == "Approved"
    current = db.query(DocumentRevision).filter_by(mdl_item_id=item.id, is_current=True).one()
    assert current.status == "final"


def test_new_reply_form_waits_for_previous_completion(client, project, users, db):
    body = _manual_afc(client, project, users, db)
    dcc = auth_headers(client, "dcc@edms-test.com")
    first = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet",
        headers=dcc,
        json={"completed": False, "mode": "no_comment"},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet",
        headers=dcc,
        json={"completed": True, "mode": "no_comment"},
    )
    assert second.status_code == 409, second.text


def test_rejected_status_is_visible_to_drawing_preparer_after_referral(client, project, users, db):
    from app.models import NotificationLog

    body = _manual_afc(client, project, users, db)
    revision_id = body["lines"][0]["revision_id"]
    comment = client.post(
        f"/api/v1/revisions/{revision_id}/comments",
        headers=auth_headers(client, "reviewer@edms-test.com"),
        json={"revision_id": revision_id, "text": "ایراد عمده در نقشه وجود دارد", "code": "1", "page_no": 1},
    )
    assert comment.status_code == 201, comment.text

    dcc = auth_headers(client, "dcc@edms-test.com")
    completed = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet",
        headers=dcc,
        json={
            "mode": "commented",
            "completed": True,
            "documents": [
                {
                    "line_id": body["lines"][0]["id"],
                    "status": "Rejected",
                    "judgment": "Rejected",
                    "comment_ids": [comment.json()["id"]],
                }
            ],
        },
    )
    assert completed.status_code == 201, completed.text
    assert completed.json()["rows"][0]["status"] == "Rejected"
    assert completed.json()["rows"][0]["doc_status"] == "Rejected"
    assert completed.json()["rows"][0]["doc_percent"] == 0

    referred = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/refer",
        headers=dcc,
        json={"to_user_id": str(users["submitter"].id), "note": "لطفاً اصلاح و ارسال مجدد شود."},
    )
    assert referred.status_code == 200, referred.text
    referred_body = referred.json()
    assert referred_body["referred_to_id"] == str(users["submitter"].id)
    assert referred_body["referred_to_name"] == "submitter"
    assert referred_body["assigned_to_id"] == str(users["submitter"].id)
    assert referred_body["referral_note"] == "لطفاً اصلاح و ارسال مجدد شود."
    assert any(event["action"] == "reply_sheet.refer" for event in referred_body["flow_history"])

    notification_row = db.query(NotificationLog).filter_by(
        user_id=users["submitter"].id,
        template="reply_sheet.referred",
        entity_id=body["id"],
    ).first()
    assert notification_row is not None
    recipient_view = client.get(
        f"/api/v1/transmittals/{body['id']}",
        headers=auth_headers(client, "submitter@edms-test.com"),
    )
    assert recipient_view.status_code == 200, recipient_view.text
    assert recipient_view.json()["reply_sheet"]["rows"][0]["status"] == "Rejected"


def test_final_afc_creates_idempotent_os_revision_b0(client, project, users, db):
    body = _manual_afc(client, project, users, db)
    dcc = auth_headers(client, "dcc@edms-test.com")
    assert client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/no-comment",
        headers=dcc,
        json={},
    ).status_code == 201
    finalized = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/no-comment-finalize",
        headers=auth_headers(client, "pm@edms-test.com"),
        json={},
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["os_available"] is True

    pm = auth_headers(client, "pm@edms-test.com")
    first = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/create-os", headers=pm, json={}
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["type"] == "OS"
    assert first_body["lines"][0]["rev"] == "B0"

    second = client.post(
        f"/api/v1/transmittals/{body['id']}/reply-sheet/create-os", headers=pm, json={}
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first_body["id"]
    assert second.json()["lines"][0]["rev"] == "B0"
    source = client.get(f"/api/v1/transmittals/{body['id']}", headers=pm)
    assert source.status_code == 200, source.text
    assert source.json()["os_transmittal_id"] == first_body["id"]

    from app.models import Transmittal

    assert db.query(Transmittal).filter_by(project_id=project.id, type="OS").count() == 1
