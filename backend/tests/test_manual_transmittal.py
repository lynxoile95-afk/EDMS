"""Manual transmittal registration from project-scoped MDL rows."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers


def test_manual_transmittal_uses_selected_mdl_rows(client: TestClient, project, users, db, ddm_rules):
    from app.models import MDLItem, DocumentRevision, Transmittal

    first = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    second = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-MEC-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/transmittals/manual",
        headers=dcc,
        json={
            "rows": [
                {"mdl_item_id": str(first.id), "revision": "A", "page_count": 3},
                {"mdl_item_id": str(second.id), "revision": "B", "page_count": 5, "note": "بازنگری دستی"},
            ],
            "purpose": "IFR",
            "type": "internal",
            "from_org": "پیمانکار",
            "to_org": "کارفرما",
            "remarks": "ترانسمیتال ثبت‌شده به‌صورت دستی",
            "extra_recipient_ids": [str(users["reviewer"].id)],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["project_id"] == str(project.id)
    assert body["remarks"] == "ترانسمیتال ثبت‌شده به‌صورت دستی"
    assert body["from_org"] == "پیمانکار"
    assert body["to_org"] == "کارفرما"
    assert len(body["lines"]) == 2
    assert any(line["rev"] == "B" for line in body["lines"])
    assert len(body["recipients"]) >= 1
    assert db.query(DocumentRevision).filter_by(project_id=project.id).count() == 2
    assert db.query(Transmittal).filter_by(project_id=project.id).count() == 1


def test_manual_ot_stores_jalali_issue_date_and_line_status(client: TestClient, project, users, db):
    from app.models import MDLItem

    item = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/transmittals/manual",
        headers=dcc,
        json={
            "rows": [
                {
                    "mdl_item_id": str(item.id),
                    "revision": "A",
                    "page_count": 3,
                    "doc_status": "Approve As Note",
                }
            ],
            "purpose": "IFR",
            "type": "OT",
            "issue_date": "۱۴۰۵/۰۸/۰۲",
            "final_approver_id": str(users["pm"].id),
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["manual_workflow"] is True
    assert body["issue_date"] == "2026-10-24"
    assert body["final_approver_id"] == str(users["pm"].id)
    assert body["recipients"] == []
    assert body["lines"][0]["doc_status"] == "Approve As Note"


def test_manual_referral_accepts_per_person_action_and_note(client: TestClient, project, users, db):
    from app.models import MDLItem

    item = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")
    created = client.post(
        f"/api/v1/projects/{project.id}/transmittals/manual",
        headers=dcc,
        json={
            "rows": [{"mdl_item_id": str(item.id), "revision": "A", "doc_status": "No Comment"}],
            "type": "OT",
            "issue_date": "1405/08/02",
            "final_approver_id": str(users["pm"].id),
        },
    )
    assert created.status_code == 201, created.text
    transmittal_id = created.json()["id"]

    issued = client.post(
        f"/api/v1/transmittals/{transmittal_id}/issue",
        headers=dcc,
        json={"notify": False},
    )
    assert issued.status_code == 200, issued.text

    referred = client.post(
        f"/api/v1/transmittals/{transmittal_id}/refer-people",
        headers=dcc,
        json={
            "people": [
                {"user_id": str(users["reviewer"].id), "action": "review", "note": "بررسی و اعلام نظر"},
                {"user_id": str(users["pm"].id), "action": "return_to_sender", "note": "بازگشت به فرستنده برای اصلاح"},
            ],
            "due_days": 14,
        },
    )
    assert referred.status_code == 200, referred.text
    referrals = {row["to_user_id"]: row for row in referred.json()["referrals"]}
    assert referrals[str(users["reviewer"].id)]["action"] == "review"
    assert referrals[str(users["reviewer"].id)]["note"] == "بررسی و اعلام نظر"
    assert referrals[str(users["pm"].id)]["action"] == "return_to_sender"
    assert referrals[str(users["pm"].id)]["note"] == "بازگشت به فرستنده برای اصلاح"


def test_manual_transmittal_rejects_a_row_from_another_project(client: TestClient, project, users):
    dcc = auth_headers(client, "dcc@edms-test.com")
    response = client.post(
        f"/api/v1/projects/{project.id}/transmittals/manual",
        headers=dcc,
        json={
            "rows": [{"mdl_item_id": "00000000-0000-0000-0000-000000000000", "revision": "A"}],
            "extra_recipient_ids": [str(users["reviewer"].id)],
        },
    )
    assert response.status_code == 422
