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


def test_ot_os_revision_history_is_append_only_and_exposed_as_document_records(client: TestClient, project, users, db):
    """A0/A1/A2 remain visible as Void rows when B0 (OS) is created."""
    from app.models import MDLItem

    item = db.query(MDLItem).filter_by(project_id=project.id, doc_code_norm="TST-CIV-DR-001").one()
    dcc = auth_headers(client, "dcc@edms-test.com")

    def create_and_issue(transmittal_type: str) -> dict:
        created = client.post(
            f"/api/v1/projects/{project.id}/transmittals/manual",
            headers=dcc,
            json={
                "rows": [{"mdl_item_id": str(item.id), "revision": "A", "doc_status": "No Comment"}],
                "type": transmittal_type,
                "issue_date": "1405/08/02",
                "final_approver_id": str(users["pm"].id),
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        issued = client.post(
            f"/api/v1/transmittals/{body['id']}/issue",
            headers=dcc,
            json={"notify": False},
        )
        assert issued.status_code == 200, issued.text
        return body

    first = create_and_issue("OT")
    second = create_and_issue("OT")
    third = create_and_issue("OT")
    assert [first["lines"][0]["rev"], second["lines"][0]["rev"], third["lines"][0]["rev"]] == ["A0", "A1", "A2"]

    before_os = client.get(
        f"/api/v1/mdl-items/{item.id}/document-records", headers=dcc
    )
    assert before_os.status_code == 200, before_os.text
    assert [row["version"] for row in before_os.json()] == ["A2", "A1", "A0"]
    assert [row["status"] for row in before_os.json()] == ["issued", "void", "void"]
    assert before_os.json()[0]["is_latest"] is True

    os_transmittal = create_and_issue("OS")
    assert os_transmittal["lines"][0]["rev"] == "B0"
    after_os = client.get(f"/api/v1/mdl-items/{item.id}/document-records", headers=dcc)
    assert after_os.status_code == 200, after_os.text
    assert [row["version"] for row in after_os.json()] == ["B0", "A2", "A1", "A0"]
    assert [row["status"] for row in after_os.json()] == ["issued", "void", "void", "void"]
    assert after_os.json()[0]["is_latest"] is True

    second_os = create_and_issue("OS")
    assert second_os["lines"][0]["rev"] == "B1"
    after_second_os = client.get(f"/api/v1/mdl-items/{item.id}/document-records", headers=dcc)
    assert [row["version"] for row in after_second_os.json()] == ["B1", "B0", "A2", "A1", "A0"]
    assert [row["status"] for row in after_second_os.json()] == ["issued", "void", "void", "void", "void"]
    assert after_second_os.json()[0]["is_latest"] is True


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
        headers=auth_headers(client, "reviewer@edms-test.com"),
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
